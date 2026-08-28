#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
namespace {
using Clock=std::chrono::steady_clock;
struct Scored{float score;std::uint32_t position,rank;};
bool better(const Scored&a,const Scored&b){return a.score!=b.score?a.score>b.score:a.rank<b.rank;}
std::size_t count(const nlohmann::json&p){std::size_t n=1;for(const auto&x:p.at("shape"))n*=x.get<std::size_t>();return n;}
template<class T>std::vector<T> read(const std::filesystem::path&root,const nlohmann::json&p){auto path=root/p.at("file").get<std::string>();if(agent_memory::sha256_file_hex(path)!=p.at("sha256"))throw std::runtime_error("int6 payload hash differs");std::ifstream f(path,std::ios::binary);std::vector<T>v(count(p));f.read(reinterpret_cast<char*>(v.data()),static_cast<std::streamsize>(v.size()*sizeof(T)));if(!f||f.peek()!=std::ifstream::traits_type::eof())throw std::runtime_error("int6 payload size differs");return v;}
std::uint8_t unpack(const std::uint8_t*row,std::size_t dim){auto bit=dim*6,byte=bit/8,shift=bit%8;std::uint16_t v=row[byte]>>shift;if(shift+6>8)v|=std::uint16_t(row[byte+1])<<(8-shift);return v&63U;}
void u32(std::vector<std::uint8_t>&b,std::uint32_t v){for(int i=0;i<4;++i)b.push_back((v>>(8*i))&255U);}
std::string seq(const std::vector<std::uint32_t>&v){std::vector<std::uint8_t>b;for(auto x:v)u32(b,x);return agent_memory::sha256_bytes_hex(b);}
std::vector<std::uint32_t> rank(const std::uint8_t*codes,const float*scales,const std::uint32_t*positions,const std::uint32_t*ranks,const float*q){std::vector<Scored>s;s.reserve(64);for(std::size_t d=0;d<64;++d){float score=0;const auto*row=codes+d*288;for(std::size_t k=0;k<384;++k)score+=float(int(unpack(row,k))-31)*q[k];s.push_back({score*scales[d],positions[d],ranks[d]});}std::nth_element(s.begin(),s.begin()+10,s.end(),better);s.resize(10);std::sort(s.begin(),s.end(),better);std::vector<std::uint32_t>v;for(auto&x:s)v.push_back(x.position);return v;}
double ms(Clock::time_point a,Clock::time_point b){return std::chrono::duration<double,std::milli>(b-a).count();}
double quant(std::vector<double>v,double f){std::sort(v.begin(),v.end());double p=f*(v.size()-1);auto a=std::size_t(std::floor(p)),b=std::size_t(std::ceil(p));return v[a]*(b-p)+v[b]*(p-a);}
nlohmann::json summary(const std::vector<double>&v){return{{"mean",std::accumulate(v.begin(),v.end(),0.)/v.size()},{"p50",quant(v,.5)},{"p95",quant(v,.95)},{"p99",quant(v,.99)},{"samples",v.size()}};}
nlohmann::json execute(const std::filesystem::path&manifest_path,bool timing){nlohmann::json m;std::ifstream(manifest_path)>>m;if(m.value("family","")!="neuroute_int6_codec_native_materialization")throw std::runtime_error("int6 manifest differs");auto root=manifest_path.parent_path();auto warm=m["timing"]["warmups"].get<std::size_t>(),passes=m["timing"]["passes"].get<std::size_t>(),batch=m["timing"]["microbatch"].get<std::size_t>();nlohmann::json rows=nlohmann::json::array();for(const auto&ds:m["datasets"]){auto dr=root/ds["id"].get<std::string>();auto queries=read<float>(dr,ds["query_vectors"]);auto qc=ds["query_count"].get<std::size_t>();for(const auto&route:ds["routes"]){auto rr=dr/std::to_string(route["seed"].get<std::uint32_t>());auto codes=read<std::uint8_t>(rr,route["codes"]);auto scales=read<float>(rr,route["scales"]);auto ranks=read<std::uint32_t>(rr,route["ranks"]);auto positions=read<std::uint32_t>(rr,route["pools"]);std::vector<std::uint8_t>digest;for(std::size_t q=0;q<qc;++q){auto v=rank(codes.data()+q*64*288,scales.data()+q*64,positions.data()+q*64,ranks.data()+q*64,queries.data()+q*384);if(seq(v)!=route["expected"][q]["ranked_sha256"])throw std::runtime_error("int6 ranking replay differs");u32(digest,q);u32(digest,v.size());for(auto x:v)u32(digest,x);}nlohmann::json t=nullptr;if(timing){for(std::size_t w=0;w<warm;++w)for(std::size_t q=0;q<qc;++q)for(std::size_t b=0;b<batch;++b)rank(codes.data()+q*64*288,scales.data()+q*64,positions.data()+q*64,ranks.data()+q*64,queries.data()+q*384);std::vector<double>samples;for(std::size_t p=0;p<passes;++p)for(std::size_t q=0;q<qc;++q){auto a=Clock::now();for(std::size_t b=0;b<batch;++b)rank(codes.data()+q*64*288,scales.data()+q*64,positions.data()+q*64,ranks.data()+q*64,queries.data()+q*384);samples.push_back(ms(a,Clock::now())/batch);}t=summary(samples);}rows.push_back({{"dataset",ds["id"]},{"seed",route["seed"]},{"query_count",qc},{"sequence_sha256",agent_memory::sha256_bytes_hex(digest)},{"timing_ms_per_query",t}});}}
return{{"schema_version",1},{"family","neuroute_int6_codec_native_result"},{"materialization_sha256",agent_memory::sha256_file_hex(manifest_path)},{"evaluator_source_manifest_sha256",AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256},{"timings_recorded",timing},{"rows",rows}};}
}
int main(int argc,char**argv){try{if(argc==2&&std::string(argv[1])=="--self-test"){std::uint8_t b[3]={31,0,0};if(unpack(b,0)!=31)throw std::runtime_error("int6 self-test differs");std::cout<<"NeuRoute INT6 native self-test passed\n";return 0;}if(argc==4&&std::string(argv[1])=="--validate"){nlohmann::json expected;std::ifstream(argv[3])>>expected;auto replay=execute(argv[2],false);if(expected["materialization_sha256"]!=replay["materialization_sha256"]||expected["evaluator_source_manifest_sha256"]!=replay["evaluator_source_manifest_sha256"]||expected["rows"].size()!=replay["rows"].size())throw std::runtime_error("int6 native report binding differs");for(std::size_t i=0;i<replay["rows"].size();++i)if(expected["rows"][i]["sequence_sha256"]!=replay["rows"][i]["sequence_sha256"])throw std::runtime_error("int6 native sequence differs");return 0;}if(argc!=3)return 2;auto report=execute(argv[1],true);std::ofstream(argv[2])<<report.dump(2)<<'\n';return 0;}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}

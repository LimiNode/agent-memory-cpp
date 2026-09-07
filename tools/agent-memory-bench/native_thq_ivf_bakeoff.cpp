#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
constexpr std::size_t D = 384;
template<class T> std::vector<T> read_file(const std::string& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream) throw std::runtime_error("cannot open " + path);
    const auto size = stream.tellg();
    if (size < 0 || static_cast<std::size_t>(size) % sizeof(T) != 0)
        throw std::runtime_error("payload alignment differs: " + path);
    std::vector<T> result(static_cast<std::size_t>(size) / sizeof(T));
    stream.seekg(0); stream.read(reinterpret_cast<char*>(result.data()), size);
    if (!stream) throw std::runtime_error("cannot read " + path);
    return result;
}
struct Input {
    std::size_t n = 0, q = 0, nlist = 0;
    std::vector<float> docs, queries, centroids, scalar_min, scalar_max;
    std::vector<std::uint8_t> thq, thq_queries, int10, int12;
    std::vector<std::int64_t> teacher, qrels;
    std::vector<float> qrel_scores;
    std::vector<std::uint32_t> order, offsets;
};
std::string ref(const json& m, const char* key) {
    return m.at("references").at(key).at("path").get<std::string>();
}
Input load(const std::string& ivf_path, const std::string& thq_path) {
    std::ifstream ivf_stream(ivf_path); if (!ivf_stream) throw std::runtime_error("IVF manifest missing");
    json m; ivf_stream >> m;
    if (m.value("family", "") != "native_thq_ivf_materialization_v1") throw std::runtime_error("IVF manifest family differs");
    std::ifstream thq_stream(thq_path); if (!thq_stream) throw std::runtime_error("THQ manifest missing");
    json t; thq_stream >> t;
    const auto path = [](const json& row) { return row.at("path").get<std::string>(); };
    Input x; x.n = m.at("documents"); x.nlist = m.at("nlist"); x.q = t.at("queries");
    x.docs = read_file<float>(ref(m, "document_vectors"));
    x.centroids = read_file<float>(ref(m, "centroids")); x.order = read_file<std::uint32_t>(ref(m, "posting_order")); x.offsets = read_file<std::uint32_t>(ref(m, "posting_offsets"));
    x.queries = read_file<float>(path(t["references"]["queries"])); x.teacher = read_file<std::int64_t>(path(t["references"]["teacher_ids"])); x.qrels = read_file<std::int64_t>(path(t["references"]["qrel_ids"])); x.qrel_scores = read_file<float>(path(t["references"]["qrel_scores"]));
    x.thq = read_file<std::uint8_t>(path(t["outputs"]["thq4_document_codes"])); x.thq_queries = read_file<std::uint8_t>(path(t["outputs"]["thq4_query_codes"])); x.int10 = read_file<std::uint8_t>(path(t["outputs"]["int10_document_codes"])); x.int12 = read_file<std::uint8_t>(path(t["outputs"]["int12_document_codes"])); x.scalar_min = read_file<float>(path(t["outputs"]["scalar_minimum"])); x.scalar_max = read_file<float>(path(t["outputs"]["scalar_maximum"]));
    if (x.docs.size()!=x.n*D || x.centroids.size()!=x.nlist*D || x.order.size()!=x.n || x.offsets.size()!=x.nlist+1 || x.queries.size()!=x.q*D || x.teacher.size()!=x.q*10) throw std::runtime_error("IVF payload shape differs");
    return x;
}
float dot(const float* a, const float* b) { float s=0; for (std::size_t i=0;i<D;++i) s+=a[i]*b[i]; return s; }
unsigned pop8(std::uint8_t v) { unsigned n=0; while(v){v&=static_cast<std::uint8_t>(v-1);++n;} return n; }
std::uint16_t hamming(const std::uint8_t* a,const std::uint8_t* b,std::size_t bytes){std::uint16_t s=0;for(std::size_t i=0;i<bytes;++i)s=static_cast<std::uint16_t>(s+pop8(static_cast<std::uint8_t>(a[i]^b[i])));return s;}
std::uint16_t level(const std::uint8_t* c,std::size_t coordinate,unsigned bits){std::uint16_t v=0;const auto base=coordinate*bits;for(unsigned b=0;b<bits;++b)v|=static_cast<std::uint16_t>(((c[(base+b)/8]>>((base+b)%8))&1U)<<b);return v;}
float scalar_dot(const std::uint8_t* c,const float* q,const Input& x,unsigned bits){float s=0;const float levels=static_cast<float>((1U<<bits)-1U);for(std::size_t i=0;i<D;++i){const float span=std::max(x.scalar_max[i]-x.scalar_min[i],1.0e-8F);s+=(x.scalar_min[i]+static_cast<float>(level(c,i,bits))*span/levels)*q[i];}return s;}
double overlap(const std::vector<std::uint32_t>& ids,const Input& x,std::size_t qi){std::size_t h=0;for(auto id:ids)for(std::size_t r=0;r<10;++r)if(x.teacher[qi*10+r]==static_cast<std::int64_t>(id))++h;return static_cast<double>(h)/10.0;}
double ndcg(const std::vector<std::uint32_t>& ids,const Input& x,std::size_t qi){std::vector<float> ideal;for(std::size_t i=0;i<20;++i)if(x.qrels[qi*20+i]>=0)ideal.push_back(x.qrel_scores[qi*20+i]);std::sort(ideal.rbegin(),ideal.rend());double n=0,d=0;for(std::size_t i=0;i<std::min<std::size_t>(10,ideal.size());++i)d+=(std::pow(2.0,ideal[i])-1.0)/std::log2(i+2.0);for(std::size_t i=0;i<std::min<std::size_t>(10,ids.size());++i){float grade=0;for(std::size_t j=0;j<20;++j)if(x.qrels[qi*20+j]==static_cast<std::int64_t>(ids[i])){grade=x.qrel_scores[qi*20+j];break;}n+=(std::pow(2.0,grade)-1.0)/std::log2(i+2.0);}return d?n/d:0;}
double percentile(std::vector<double> v,double f){std::sort(v.begin(),v.end());return v[static_cast<std::size_t>(f*(v.size()-1))];}
}
int main(int argc,char** argv){try{if(argc<4||argc>6){std::cerr<<"usage: native_thq_ivf_bakeoff <ivf-manifest> <thq-manifest> <output> [query-limit] [repeats]\n";return 2;}auto x=load(argv[1],argv[2]);if(argc>=5)x.q=std::min(x.q,static_cast<std::size_t>(std::stoul(argv[4])));const int repeats=argc>=6?std::stoi(argv[5]):2;const std::array<std::size_t,5> budgets{{20000,50000,100000,200000,400000}};json report{{"schema_version",1},{"family","native_thq_ivf_bakeoff_result_v1"},{"documents",x.n},{"queries",x.q},{"nlist",x.nlist},{"budgets",budgets},{"repeats",repeats},{"rows",json::array()}};for(auto budget:budgets){std::vector<double> route_survival,thq_survival,fp_scores,i10_scores,i12_scores,times;std::size_t candidates_total=0;for(std::size_t qi=0;qi<x.q;++qi)for(int rep=0;rep<repeats;++rep){const auto begin=Clock::now();std::vector<float> coarse(x.nlist);for(std::size_t c=0;c<x.nlist;++c)coarse[c]=dot(x.centroids.data()+c*D,x.queries.data()+qi*D);std::vector<std::uint32_t> cells(x.nlist);std::iota(cells.begin(),cells.end(),0U);std::sort(cells.begin(),cells.end(),[&](auto a,auto b){return coarse[a]==coarse[b]?a<b:coarse[a]>coarse[b];});std::vector<std::uint32_t> candidates;candidates.reserve(budget);for(auto cell:cells){const auto first=x.offsets[cell],last=x.offsets[cell+1];if(!candidates.empty()&&candidates.size()+last-first>budget)break;candidates.insert(candidates.end(),x.order.begin()+first,x.order.begin()+last);if(candidates.size()>=budget)break;}std::sort(candidates.begin(),candidates.end());std::vector<std::uint16_t> distances(candidates.size());const auto* qc=x.thq_queries.data()+qi*144;for(std::size_t i=0;i<candidates.size();++i)distances[i]=hamming(x.thq.data()+candidates[i]*144,qc,144);std::vector<std::uint32_t> pos(candidates.size());std::iota(pos.begin(),pos.end(),0U);std::partial_sort(pos.begin(),pos.begin()+std::min<std::size_t>(256,pos.size()),pos.end(),[&](auto a,auto b){return distances[a]==distances[b]?candidates[a]<candidates[b]:distances[a]<distances[b];});pos.resize(std::min<std::size_t>(256,pos.size()));std::vector<std::uint32_t> shortlist;for(auto p:pos)shortlist.push_back(candidates[p]);auto select=[&](unsigned bits,const std::vector<std::uint8_t>& codes){std::vector<std::pair<float,std::uint32_t>> scored;scored.reserve(shortlist.size());for(auto id:shortlist){float s=bits==0?dot(x.docs.data()+id*D,x.queries.data()+qi*D):scalar_dot(codes.data()+id*(D*bits/8),x.queries.data()+qi*D,x,bits);scored.emplace_back(s,id);}std::sort(scored.begin(),scored.end(),[](auto a,auto b){return a.first==b.first?a.second<b.second:a.first>b.first;});std::vector<std::uint32_t> ids;for(std::size_t i=0;i<std::min<std::size_t>(10,scored.size());++i)ids.push_back(scored[i].second);return ids;};auto fp=select(0,x.int10),i10=select(10,x.int10),i12=select(12,x.int12);route_survival.push_back(overlap(candidates,x,qi));thq_survival.push_back(overlap(shortlist,x,qi));fp_scores.push_back(ndcg(fp,x,qi));i10_scores.push_back(ndcg(i10,x,qi));i12_scores.push_back(ndcg(i12,x,qi));times.push_back(std::chrono::duration<double,std::milli>(Clock::now()-begin).count());candidates_total+=candidates.size();}report["rows"].push_back({{"budget",budget},{"mean_candidates",static_cast<double>(candidates_total)/(x.q*repeats)},{"teacher_survival_mean",std::accumulate(route_survival.begin(),route_survival.end(),0.0)/route_survival.size()},{"teacher_survival_thq256_mean",std::accumulate(thq_survival.begin(),thq_survival.end(),0.0)/thq_survival.size()},{"ndcg_fp32_mean",std::accumulate(fp_scores.begin(),fp_scores.end(),0.0)/fp_scores.size()},{"ndcg_int10_mean",std::accumulate(i10_scores.begin(),i10_scores.end(),0.0)/i10_scores.size()},{"ndcg_int12_mean",std::accumulate(i12_scores.begin(),i12_scores.end(),0.0)/i12_scores.size()},{"query_ms_p50",percentile(times,.5)},{"query_ms_p95",percentile(times,.95)},{"query_ms_p99",percentile(times,.99)},{"logical_bytes_per_query",budget*148+x.nlist*D*sizeof(float)}});}std::ofstream out(argv[3]);out<<report.dump(2)<<'\n';return 0;}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}

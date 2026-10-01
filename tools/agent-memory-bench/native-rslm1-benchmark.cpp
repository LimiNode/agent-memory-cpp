#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
constexpr std::size_t D = 384, BLOCK = 128, SYMBOL_BYTES = 48,
                     THQ_BYTES = 96, DOCUMENTS = 1000000;
struct Candidate { double score; std::int32_t id; };

template <typename T> std::vector<T> read(const std::string& path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw std::runtime_error("cannot open " + path);
  auto end = in.tellg();
  if (end < 0 || static_cast<std::size_t>(end) % sizeof(T) != 0)
    throw std::runtime_error("unaligned input " + path);
  std::vector<T> out(static_cast<std::size_t>(end) / sizeof(T));
  in.seekg(0); in.read(reinterpret_cast<char*>(out.data()), end);
  if (!in) throw std::runtime_error("cannot read " + path);
  return out;
}

constexpr std::array<float, 64> C4 = {
 -0.667223f,-1.156572f,0.867890f,0.619376f, -0.000491f,-0.000645f,-0.001313f,-0.002269f,
  1.188668f,-0.673327f,0.886783f,0.485405f, -0.467725f,0.209985f,-0.069217f,1.624921f,
 -0.229568f,-1.455663f,-0.377640f,-0.769526f, 0.511224f,-1.060941f,-0.839085f,0.904128f,
  1.366460f,-0.348822f,-0.473865f,-0.835606f, -0.532846f,0.303232f,-0.567821f,-1.482903f,
  0.698791f,1.232136f,0.409275f,-0.842221f, 0.173459f,-0.451141f,1.204719f,-1.121391f,
 -1.366599f,-0.395756f,-0.898790f,0.266300f, -0.683748f,1.503576f,-0.367668f,0.225374f,
 -1.431984f,0.286363f,0.771768f,-0.418472f, 0.013678f,0.749156f,1.416614f,0.582220f,
  0.228653f,0.377476f,-1.638933f,-0.070251f, 1.178144f,0.832109f,-0.293186f,0.860906f};
constexpr std::array<float, D> FLIPS = [] { std::array<float,D> a{}; const char* s =
    "+-----+--+----+++++--+++-++++-++-++++-++--+--+----++----+--+---++++-++++--+--++++---++++"
    "-+--++-++++-++++--+----+----++++-++-++--++++-++++-++-++-++-++--++--+--+-++++--++-++++--++"
    "++---++--+-+++-+++-+++++++---+-+--++++-++++++++-+--++--+--++++--+++++----++-+----";
    for(std::size_t i=0;i<D;++i)a[i]=(s[i%256]=='+'?1.0f:-1.0f); return a; }();
constexpr std::array<std::size_t, D> PERM = {106,71,37,89,32,11,101,120,19,18,24,114,103,63,58,92,44,38,76,23,20,1,95,17,45,82,74,14,86,5,13,123,117,34,53,109,40,107,115,48,49,41,3,73,52,100,22,64,80,55,6,12,26,94,113,50,87,105,127,36,90,59,46,111,102,118,35,125,65,78,42,4,110,79,126,9,0,99,81,29,108,75,2,43,116,28,31,15,57,66,56,47,83,85,51,39,91,25,88,119,96,69,27,54,77,67,33,21,70,60,84,124,16,98,68,97,8,104,62,93,122,10,121,72,112,30,7,61};

float ue7m9(std::uint16_t bits) {
  if (!bits) return 0.0f; std::uint32_t value = (static_cast<std::uint32_t>(bits) << 14U) + 0x20000000U;
  float out; std::memcpy(&out, &value, sizeof(out)); return out;
}
void fwht(float* x) {
  for (std::size_t width=1;width<BLOCK;width<<=1U)
    for (std::size_t base=0;base<BLOCK;base+=2*width)
      for (std::size_t lane=0;lane<width;++lane) { float a=x[base+lane], b=x[base+width+lane]; x[base+lane]=a+b; x[base+width+lane]=a-b; }
  const float scale=1.0f/std::sqrt(128.0f); for(std::size_t i=0;i<BLOCK;++i)x[i]*=scale;
}
std::array<float,D> decode_residual(const std::uint8_t* symbols, std::uint16_t scale) {
  std::array<float,D> x{};
  for(std::size_t g=0;g<D/4;++g){const auto packed=symbols[g/2]; const auto sym=static_cast<std::size_t>((g&1U)?(packed&15U):(packed>>4U)); for(std::size_t lane=0;lane<4;++lane)x[g*4+lane]=C4[sym*4+lane]*ue7m9(scale);}
  for(std::size_t block=0;block<3;++block)fwht(x.data()+block*BLOCK);
  for(std::size_t i=0;i<D;++i)x[i]*=FLIPS[(i+127)%256];
  std::array<float,D> un{}; for(std::size_t block=0;block<3;++block)for(std::size_t i=0;i<BLOCK;++i)un[block*BLOCK+PERM[i]]=x[i*3+block];
  for(std::size_t block=0;block<3;++block)fwht(un.data()+block*BLOCK);
  for(std::size_t i=0;i<D;++i)un[i]*=FLIPS[i%256]; return un;
}
std::array<float,D> rotate_forward(std::array<float,D> x) {
  for(std::size_t i=0;i<D;++i)x[i]*=FLIPS[i%256];
  for(std::size_t block=0;block<3;++block)fwht(x.data()+block*BLOCK);
  std::array<float,D> inter{};
  for(std::size_t block=0;block<3;++block)
    for(std::size_t i=0;i<BLOCK;++i) inter[i*3+block]=x[block*BLOCK+PERM[i]];
  for(std::size_t i=0;i<D;++i)inter[i]*=FLIPS[(i+127)%256];
  for(std::size_t block=0;block<3;++block)fwht(inter.data()+block*BLOCK);
  return inter;
}
double direct_score(const std::uint8_t* symbols, std::uint16_t inner,
                    const std::uint8_t* thq_row, const float* centroids,
                    const float* query, const std::array<float,D>& query_rot,
                    double query_norm) {
  std::array<float,D> base{};
  for(std::size_t byte=0;byte<THQ_BYTES;++byte) {
    const auto packed=thq_row[byte];
    for(std::size_t lane=0;lane<4;++lane) {
      const auto d=byte*4+lane; const auto level=(packed>>(lane*2U))&3U;
      base[d]=centroids[d*4+level];
    }
  }
  const auto base_rot=rotate_forward(base); const float scale=ue7m9(inner);
  // The positive outer UE7M9 scale multiplies the full reconstructed vector,
  // so it cancels from cosine ordering and is intentionally absent here.
  double dot=0.0, norm2=0.0, residual_norm2=0.0, cross=0.0;
  for(std::size_t d=0;d<D;++d) { dot += static_cast<double>(base[d])*query[d]; norm2 += static_cast<double>(base[d])*base[d]; }
  for(std::size_t g=0;g<D/4;++g) {
    const auto packed=symbols[g/2]; const auto symbol=static_cast<std::size_t>((g&1U)?(packed&15U):(packed>>4U));
    for(std::size_t lane=0;lane<4;++lane) {
      const float value=C4[symbol*4+lane]*scale; const auto d=g*4+lane;
      dot += static_cast<double>(value)*query_rot[d]; residual_norm2 += static_cast<double>(value)*value; cross += static_cast<double>(value)*base_rot[d];
    }
  }
  norm2 += residual_norm2 + 2.0*cross;
  return dot / std::sqrt(std::max(norm2, 1e-30) * std::max(query_norm*query_norm, 1e-30));
}
bool order(const Candidate&a,const Candidate&b){return a.score>b.score||(a.score==b.score&&a.id<b.id);}
std::vector<std::int32_t> top10(std::vector<Candidate> v){std::sort(v.begin(),v.end(),order);std::vector<std::int32_t> out;for(std::size_t i=0;i<std::min<std::size_t>(10,v.size());++i)out.push_back(v[i].id);return out;}

int run_matched(int argc, char** argv) {
  if (argc != 13 && argc != 15) throw std::runtime_error("usage: --matched symbols inner outer ids thq centroids thresholds candidate_flat offsets queries raw [warmups repeats]");
  auto symbols=read<std::uint8_t>(argv[2]); auto inner=read<std::uint16_t>(argv[3]); auto outer=read<std::uint16_t>(argv[4]); auto ids=read<std::int32_t>(argv[5]); auto thq=read<std::uint8_t>(argv[6]); auto cent=read<float>(argv[7]); auto thresholds=read<float>(argv[8]); auto candidate=read<std::uint8_t>(argv[9]); auto offsets=read<std::uint64_t>(argv[10]); auto queries=read<float>(argv[11]);
  const auto record_bytes = offsets.size() == 153 && offsets.back() != 0 && candidate.size() % offsets.back() == 0 ? candidate.size() / offsets.back() : 0;
  if(ids.empty()||symbols.size()!=ids.size()*SYMBOL_BYTES||inner.size()!=ids.size()||outer.size()!=ids.size()||cent.size()!=D*4||thq.size()!=DOCUMENTS*THQ_BYTES||thresholds.size()!=D*3||queries.size()!=152*D||offsets.size()!=153||(record_bytes!=100&&record_bytes!=148)) throw std::runtime_error("matched RSLM fixture shape differs");
  if(!std::is_sorted(ids.begin(),ids.end())||std::adjacent_find(ids.begin(),ids.end())!=ids.end()) throw std::runtime_error("RSLM1 payload IDs are not strictly sorted");
  // Materialize the immutable ID-to-payload-row map before the timed loop.
  // Candidate pages may share documents, so the payload is deduplicated rather
  // than laid out as query_count * BLOCK rows.
  std::unordered_map<std::int32_t, std::size_t> row_by_id;
  row_by_id.reserve(ids.size());
  for (std::size_t row = 0; row < ids.size(); ++row)
    row_by_id.emplace(ids[row], row);
  std::vector<std::size_t> rows(0); std::vector<double> timings;
  std::ofstream raw(argv[12]);
  if(!raw) throw std::runtime_error("cannot open matched RSLM raw output");
  const std::size_t warmups = argc == 15 ? static_cast<std::size_t>(std::stoul(argv[13])) : 0;
  const std::size_t repeats = argc == 15 ? static_cast<std::size_t>(std::stoul(argv[14])) : 1;
  if (repeats == 0) throw std::runtime_error("RSLM repeats must be positive");
  std::size_t query_parity=0;
  for(std::size_t qi=0;qi<152;++qi){
    for (std::size_t repeat = 0; repeat < warmups + repeats; ++repeat) {
    const auto start=std::chrono::steady_clock::now();
    const float* query=queries.data()+qi*D; std::array<float,D> qarray{}; std::copy(query,query+D,qarray.begin()); const auto qrot=rotate_forward(qarray); const double qnorm=std::sqrt(std::inner_product(query,query+D,query,0.0));
    std::array<float,D*4> coord{}; for(std::size_t d=0;d<D;++d) for(std::size_t l=0;l<4;++l){const float lo=l==0?-std::numeric_limits<float>::infinity():thresholds[d*3+l-1]; const float hi=l==3?std::numeric_limits<float>::infinity():thresholds[d*3+l]; const float v=query[d]; const float delta=v<lo?lo-v:(v>hi?v-hi:0.0f); coord[d*4+l]=delta*delta;}
    const auto begin=static_cast<std::size_t>(offsets[qi]), end=static_cast<std::size_t>(offsets[qi+1]); std::vector<Candidate> coarse; coarse.reserve(end-begin);
    for(std::size_t p=begin;p<end;++p){std::int32_t id=0; std::memcpy(&id,candidate.data()+p*record_bytes,4); const auto* row=thq.data()+static_cast<std::size_t>(id)*THQ_BYTES; double score=0.0; for(std::size_t b=0;b<THQ_BYTES;++b){const auto packed=row[b]; for(std::size_t lane=0;lane<4;++lane) score+=coord[(b*4+lane)*4+((packed>>(lane*2))&3U)];} coarse.push_back({score,id});}
    if(coarse.size()<BLOCK) throw std::runtime_error("matched RSLM candidate page is narrower than top128");
    std::partial_sort(coarse.begin(),coarse.begin()+BLOCK,coarse.end(),[](const Candidate&a,const Candidate&b){return a.score<b.score||(a.score==b.score&&a.id<b.id);});
    std::vector<Candidate> scored; scored.reserve(BLOCK);
    for(std::size_t i=0;i<BLOCK;++i){const auto id=coarse[i].id; auto it=row_by_id.find(id); if(it==row_by_id.end()) throw std::runtime_error("matched RSLM top128 ID missing from payload"); const auto row=it->second; scored.push_back({direct_score(symbols.data()+row*SYMBOL_BYTES,inner[row],thq.data()+static_cast<std::size_t>(id)*THQ_BYTES,cent.data(),query,qrot,qnorm),id});}
    const auto elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count(); const auto top=top10(std::move(scored));
    if (repeat >= warmups) {
      timings.push_back(elapsed);
      raw<<"{\"repeat\":"<<(repeat-warmups)<<",\"query\":"<<qi<<",\"timing_ms\":"<<std::setprecision(12)<<elapsed<<",\"top10_ids\":["; for(std::size_t i=0;i<top.size();++i){if(i)raw<<',';raw<<top[i];} raw<<"]}\n";
    }
    }
  }
  std::sort(timings.begin(),timings.end()); const auto pct=[&](double p){return timings[std::min(timings.size()-1,static_cast<std::size_t>(p*timings.size()))];};
  std::cout<<std::fixed<<std::setprecision(6)<<"{\"status\":\"EXECUTED\",\"scope\":\"R4 candidate stream -> THQ top128 -> RSLM1 packed scorer\",\"queries\":152,\"warmups\":"<<warmups<<",\"repeats\":"<<repeats<<",\"p50_ms\":"<<pct(.50)<<",\"p95_ms\":"<<pct(.95)<<",\"p99_ms\":"<<pct(.99)<<"}\n"; return 0;
}

void self_test(){
  std::array<std::uint8_t,SYMBOL_BYTES> symbols{}; for(std::size_t i=0;i<SYMBOL_BYTES;++i) symbols[i]=static_cast<std::uint8_t>(((i%16)<<4)|((i+1)%16));
  const auto residual=decode_residual(symbols.data(),0x5000); for(float v:residual)if(!std::isfinite(v))throw std::runtime_error("non-finite self-test");
  constexpr std::array<std::size_t,8> checks{0,1,2,63,127,128,255,383};
  constexpr std::array<float,8> expected{-2.046750609e-7F,3.417328855e-7F,1.956614853e-7F,-8.141464747e-8F,-3.387781078e-8F,-1.184007630e-7F,1.754771973e-8F,1.870040478e-8F};
  for(std::size_t i=0;i<checks.size();++i)if(std::abs(residual[checks[i]]-expected[i])>1e-12F)throw std::runtime_error("RSLM faithful golden residual differs");
  std::array<float,D> input{}; for(std::size_t i=0;i<D;++i) input[i]=static_cast<float>(i%17)-8.0F;
  const auto rotated=rotate_forward(input); std::array<float,D> roundtrip=rotated;
  for(std::size_t block=0;block<3;++block)fwht(roundtrip.data()+block*BLOCK);
  for(std::size_t i=0;i<D;++i)roundtrip[i]*=FLIPS[(i+127)%256];
  std::array<float,D> un{}; for(std::size_t block=0;block<3;++block)for(std::size_t i=0;i<BLOCK;++i)un[block*BLOCK+PERM[i]]=roundtrip[i*3+block];
  for(std::size_t block=0;block<3;++block)fwht(un.data()+block*BLOCK); for(std::size_t i=0;i<D;++i)un[i]*=FLIPS[i%256];
  float max_error=0.0F; for(std::size_t i=0;i<D;++i)max_error=std::max(max_error,std::abs(un[i]-input[i]));
  if(max_error>1e-4F || std::abs(ue7m9(0x5000)-1.1920928955078125e-7F)>1e-12F)throw std::runtime_error("RSLM golden transform self-test failed");
  std::cout<<"native-rslm1-benchmark self-test PASS\n";
}
}

int main(int argc,char**argv){
 try {
  if(argc==2&&std::string(argv[1])=="--self-test"){self_test();return 0;}
  if(argc>=2&&std::string(argv[1])=="--matched") return run_matched(argc,argv);
  if(argc!=11||std::string(argv[1])!="--candidate-gate") throw std::runtime_error("usage: --candidate-gate symbols inner outer ids thq_codes centroids candidate_ids offsets queries");
  auto symbols=read<std::uint8_t>(argv[2]); auto inner=read<std::uint16_t>(argv[3]); auto outer=read<std::uint16_t>(argv[4]); auto ids=read<std::int32_t>(argv[5]); auto thq=read<std::uint8_t>(argv[6]); auto cent=read<float>(argv[7]); auto candidate=read<std::int32_t>(argv[8]); auto offsets=read<std::uint64_t>(argv[9]); auto queries=read<float>(argv[10]);
  if(ids.empty()||symbols.size()!=ids.size()*SYMBOL_BYTES||inner.size()!=ids.size()||outer.size()!=ids.size()||cent.size()!=D*4||thq.size()!=DOCUMENTS*THQ_BYTES||queries.size()!=152*D||offsets.size()!=153||candidate.size()!=offsets.back()) throw std::runtime_error("RSLM1 input shape differs: ids="+std::to_string(ids.size())+" symbols="+std::to_string(symbols.size())+" inner="+std::to_string(inner.size())+" outer="+std::to_string(outer.size())+" cent="+std::to_string(cent.size())+" thq="+std::to_string(thq.size())+" queries="+std::to_string(queries.size())+" offsets="+std::to_string(offsets.size())+" candidate="+std::to_string(candidate.size())+" last="+std::to_string(offsets.back()));
  if(!std::is_sorted(ids.begin(),ids.end())||std::adjacent_find(ids.begin(),ids.end())!=ids.end()) throw std::runtime_error("RSLM1 payload IDs are not strictly sorted");
  std::vector<std::size_t> candidate_rows(candidate.size());
  for(std::size_t p=0;p<candidate.size();++p){auto it=std::lower_bound(ids.begin(),ids.end(),candidate[p]);if(it==ids.end()||*it!=candidate[p])throw std::runtime_error("candidate ID missing from RSLM payload");candidate_rows[p]=static_cast<std::size_t>(it-ids.begin());}
  for(std::size_t qi=0;qi<152;++qi){
    const auto begin=offsets[qi], end=offsets[qi+1]; if(end<begin||end>candidate.size()) throw std::runtime_error("invalid candidate offsets");
    std::vector<Candidate> scored; scored.reserve(static_cast<std::size_t>(end-begin));
    const float* query=queries.data()+qi*D; std::array<float,D> query_array{}; std::copy(query,query+D,query_array.begin()); const auto query_rot=rotate_forward(query_array); const double query_norm=std::sqrt(std::inner_product(query,query+D,query,0.0));
    const auto start=std::chrono::steady_clock::now();
    for(std::uint64_t p=begin;p<end;++p){
      const auto position=static_cast<std::size_t>(p); const auto id=candidate[position]; const auto row=candidate_rows[position];
      const auto* code=thq.data()+static_cast<std::size_t>(id)*THQ_BYTES;
      scored.push_back({direct_score(symbols.data()+row*SYMBOL_BYTES,inner[row],code,cent.data(),query,query_rot,query_norm),id});
    }
    const auto elapsed=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count(); auto top=top10(std::move(scored));
    std::cout<<"{\"query\":"<<qi<<",\"candidate_count\":"<<(end-begin)<<",\"top10_ids\":[";for(std::size_t i=0;i<top.size();++i){if(i)std::cout<<',';std::cout<<top[i];}std::cout<<"],\"timing_ms\":{\"codec_rerank\":"<<std::setprecision(10)<<elapsed<<"}}\n";
  }
 } catch(const std::exception&e){std::cerr<<"native-rslm1-benchmark: "<<e.what()<<"\n";return 2;}
}

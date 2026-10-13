#include <nlohmann/json.hpp>

#include <algorithm>
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
constexpr std::size_t D = 384, C = 4096, BITS = 256, BYTES = 32;

template <class T> std::vector<T> read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("cannot open " + path);
    in.seekg(0, std::ios::end);
    const auto bytes = static_cast<std::size_t>(in.tellg()); in.seekg(0);
    if (bytes % sizeof(T)) throw std::runtime_error("payload alignment differs: " + path);
    std::vector<T> out(bytes / sizeof(T));
    in.read(reinterpret_cast<char*>(out.data()), static_cast<std::streamsize>(bytes));
    if (!in) throw std::runtime_error("cannot read " + path);
    return out;
}

struct Model { std::vector<float> w1, b1, w2, b2; };
struct Input {
    std::size_t n = 0, q = 0;
    std::vector<float> docs, queries, mean, projection, cuts, pca, e5[4];
    std::vector<std::uint16_t> cells;
    std::vector<std::uint8_t> codes, qcodes;
    std::vector<float> qproj, adc, teacher_scores;
    std::vector<std::int64_t> teacher, qrels;
    std::vector<std::vector<std::uint32_t>> postings;
    std::vector<Model> models;
};

template <class T> std::vector<T> output_values(const json& manifest, const std::string& name,
                                                const std::string& root) {
    const auto path = root + "/" + manifest.at("outputs").at(name).at("path").get<std::string>();
    return read_file<T>(path);
}

Input load(const std::string& manifest_path) {
    std::ifstream stream(manifest_path); if (!stream) throw std::runtime_error("manifest missing");
    json m; stream >> m; const auto root = manifest_path.substr(0, manifest_path.find_last_of("\\/"));
    if (m.value("family", "") != "native_document_routing_bakeoff_materialization_v1")
        throw std::runtime_error("native document routing manifest differs");
    Input x; x.n = m.at("documents"); x.q = m.at("query_count");
    x.mean = output_values<float>(m, "mean", root); x.projection = output_values<float>(m, "projection", root);
    x.cuts = output_values<float>(m, "cuts", root); x.cells = output_values<std::uint16_t>(m, "cells", root);
    x.pca = output_values<float>(m, "pca-centroids", root);
    for (int k = 0; k != 4; ++k) x.e5[k] = output_values<float>(m, "e5-centroids-k" + std::to_string(1 << k), root);
    x.queries = output_values<float>(m, "eval_queries", root);
    x.teacher = output_values<std::int64_t>(m, "eval_teacher_ids", root);
    x.qrels = output_values<std::int64_t>(m, "eval_qrel_ids", root);
    x.teacher_scores = output_values<float>(m, "eval_qrel_scores", root);
    const auto payload = [&](const char* key) { return m.at("native_payloads").at(key).at("path").get<std::string>(); };
    x.docs = read_file<float>(payload("document_vectors")); x.codes = read_file<std::uint8_t>(payload("document_codes"));
    x.qcodes = output_values<std::uint8_t>(m, "query_codes_mapped", root);
    x.qproj = output_values<float>(m, "query_projections_mapped", root);
    x.adc = read_file<float>(payload("adc_centroids"));
    if (x.docs.size() != x.n * D || x.cells.size() != x.n || x.queries.size() != x.q * D ||
        x.teacher.size() != x.q * 10 || x.qrels.size() != x.q * 20 || x.teacher_scores.size() != x.q * 20)
        throw std::runtime_error("native document routing shape differs");
    x.postings.resize(C); for (std::size_t d = 0; d != x.n; ++d) x.postings[x.cells[d]].push_back(static_cast<std::uint32_t>(d));
    const auto models = m.at("outputs").at("direct4096");
    for (const auto& seed : {"13", "37", "101"}) {
        Model model;
        model.w1 = read_file<float>(root + "/" + models.at(seed).at("weight1").at("path").get<std::string>());
        model.b1 = read_file<float>(root + "/" + models.at(seed).at("bias1").at("path").get<std::string>());
        model.w2 = read_file<float>(root + "/" + models.at(seed).at("weight2").at("path").get<std::string>());
        model.b2 = read_file<float>(root + "/" + models.at(seed).at("bias2").at("path").get<std::string>());
        x.models.push_back(std::move(model));
    }
    return x;
}

float dot(const float* a, const float* b, std::size_t n) { float s = 0; for (std::size_t i=0;i<n;++i) s += a[i]*b[i]; return s; }
unsigned pop8(std::uint8_t v) { unsigned n=0; while(v){v&=static_cast<std::uint8_t>(v-1);++n;} return n; }
template<class T, class F> std::vector<std::uint32_t> top(const std::vector<T>& scores, std::size_t k, F less) {
    std::vector<std::uint32_t> ids(scores.size()); std::iota(ids.begin(), ids.end(), 0U);
    k = std::min(k, ids.size()); std::partial_sort(ids.begin(), ids.begin()+k, ids.end(),
        [&](std::uint32_t a, std::uint32_t b){ return less(scores[a], scores[b], a, b); }); ids.resize(k); return ids;
}
std::vector<std::uint32_t> order_desc(const std::vector<float>& scores) {
    return top(scores, scores.size(), [](float a,float b,std::uint32_t x,std::uint32_t y){return a==b?x<y:a>b;});
}
std::vector<std::uint32_t> fill(const Input& x, const std::vector<std::uint32_t>& order, std::size_t budget) {
    std::vector<std::uint32_t> docs; docs.reserve(budget);
    std::vector<bool> seen(C, false);
    for (auto cell: order) {
        if (cell >= C || seen[cell]) continue;
        seen[cell] = true;
        const auto& p=x.postings[cell];
        if (docs.size()+p.size()>budget) continue;
        docs.insert(docs.end(),p.begin(),p.end());
        if(docs.size()==budget) break;
    }
    return docs;
}
std::vector<std::uint32_t> pca_order(const Input& x, const float* q) {
    float z[12]{}; for(int i=0;i<12;++i) for(std::size_t j=0;j<D;++j) z[i]+=(q[j]-x.mean[j])*x.projection[i*D+j];
    std::vector<float> s(C); for(std::size_t c=0;c<C;++c){float v=0;for(int i=0;i<12;++i){const auto bit=((c>>i)&1U)!=0;const auto d=std::abs(z[i]-x.cuts[i]);const auto qbit=z[i]>x.cuts[i];if(bit!=qbit)v+=d;}s[c]=-v;} return order_desc(s);
}
std::vector<std::uint32_t> prepend_fallback(const std::vector<std::uint32_t>& seeds,
                                             const std::vector<std::uint32_t>& fallback,
                                             std::size_t count) {
    std::vector<std::uint32_t> result;
    result.reserve(C);
    std::vector<bool> seen(C, false);
    for (std::size_t i = 0; i < std::min(count, seeds.size()); ++i) {
        if (seeds[i] < C && !seen[seeds[i]]) {
            seen[seeds[i]] = true;
            result.push_back(seeds[i]);
        }
    }
    for (const auto cell : fallback) {
        if (cell < C && !seen[cell]) {
            seen[cell] = true;
            result.push_back(cell);
        }
    }
    return result;
}

std::vector<std::uint32_t> route_order(const Input& x, const float* q, const std::string& policy, const Model* model) {
    const auto fallback = pca_order(x,q);
    if(policy=="pca_threshold") return fallback;
    std::vector<float> s(C,-1e30f);
    float z[12]{}; for(int i=0;i<12;++i) for(std::size_t j=0;j<D;++j) z[i]+=(q[j]-x.mean[j])*x.projection[i*D+j];
    bool hybrid = false;
    std::size_t seed_count = 0;
    if(policy=="pca_centroid_k1" || policy=="pca_centroid_k1_hybrid8") {
        for(std::size_t c=0;c<C;++c){float v=0;for(int i=0;i<12;++i){const auto d=x.pca[c*12+i]-z[i];v+=d*d;}s[c]=-v;}
        hybrid = policy.find("_hybrid") != std::string::npos; seed_count = 8;
    } else if(policy.rfind("e5_centroid_k",0)==0) {
        const auto marker = policy.find("_hybrid");
        const auto k_end = marker == std::string::npos ? policy.size() : marker;
        const int k=std::stoi(policy.substr(13, k_end - 13));
        const auto& centers=x.e5[static_cast<int>(std::log2(k))];
        for(std::size_t c=0;c<C;++c)for(int a=0;a<k;++a)s[c]=std::max(s[c],dot(centers.data()+(c*k+a)*D,q,D));
        hybrid = marker != std::string::npos; seed_count = 8;
    } else if(policy.rfind("direct4096_top",0)==0) {
        const auto marker = policy.find("_hybrid");
        const auto end = marker == std::string::npos ? policy.size() : marker;
        seed_count = static_cast<std::size_t>(std::stoi(policy.substr(14, end - 14)));
        float h[128]{};
        for(int i=0;i<128;++i){h[i]=model->b1[i];for(std::size_t j=0;j<D;++j)h[i]+=(q[j]-x.mean[j])*model->w1[i*D+j];h[i]=0.5f*h[i]*(1+std::tanh(std::sqrt(2.0f/3.14159265f)*(h[i]+0.044715f*h[i]*h[i]*h[i])));}
        for(std::size_t c=0;c<C;++c)s[c]=model->b2[c];
        for(std::size_t c=0;c<C;++c)for(int i=0;i<128;++i)s[c]+=h[i]*model->w2[c*128+i];
        const auto direct = order_desc(s);
        return marker == std::string::npos ? direct : prepend_fallback(direct, fallback, seed_count);
    } else {
        throw std::runtime_error("unknown routing policy: " + policy);
    }
    const auto centroid_order = order_desc(s);
    return hybrid ? prepend_fallback(centroid_order, fallback, seed_count) : centroid_order;
}
double ndcg(const std::vector<std::uint32_t>& docs,const Input& x,std::size_t qi){double den=0,num=0;std::vector<float> ideal;for(int i=0;i<20;++i)if(x.qrels[qi*20+i]>=0)ideal.push_back(x.teacher_scores[qi*20+i]);std::sort(ideal.rbegin(),ideal.rend());for(std::size_t i=0;i<ideal.size()&&i<10;++i)den+=(std::pow(2.0,ideal[i])-1)/std::log2(double(i+2));for(std::size_t i=0;i<docs.size()&&i<10;++i){double rel=0;for(int j=0;j<20;++j)if(x.qrels[qi*20+j]==docs[i])rel=x.teacher_scores[qi*20+j];num+=(std::pow(2.0,rel)-1)/std::log2(double(i+2));}return den?num/den:0;}
struct Row { double overlap=0, qndcg=0, route=0, total=0; std::size_t candidates=0; double candidate_overlap=0, hamming_overlap=0, adc_overlap=0; std::vector<std::uint32_t> selected; };
double quantile(std::vector<double> values, double fraction) {
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    const auto index = static_cast<std::size_t>(fraction * static_cast<double>(values.size() - 1));
    return values[index];
}
Row run(const Input& x, const std::string& policy, std::size_t qi,
        std::size_t budget, const Model* model) {
    const auto begin = Clock::now();
    const auto order = route_order(x, x.queries.data() + qi * D, policy, model);
    const auto docs = fill(x, order, budget);
    const auto hits = [&](const std::vector<std::uint32_t>& values) {
        std::size_t count = 0;
        for (const auto document : values)
            for (int j = 0; j != 10; ++j)
                if (x.teacher[qi * 10 + j] == document) ++count;
        return static_cast<double>(count) / 10.0;
    };
    std::vector<std::uint16_t> hamming_distances(docs.size());
    for (std::size_t i = 0; i != docs.size(); ++i)
        for (std::size_t byte = 0; byte != BYTES; ++byte)
            hamming_distances[i] += static_cast<std::uint16_t>(pop8(
                x.codes[docs[i] * BYTES + byte] ^ x.qcodes[qi * BYTES + byte]));
    const auto hamming_positions = top<std::uint16_t>(hamming_distances, 768,
        [](auto a, auto b, std::uint32_t i, std::uint32_t j) {
            return a == b ? i < j : a < b;
        });
    std::vector<std::uint32_t> hamming(hamming_positions.size());
    for (std::size_t i = 0; i != hamming.size(); ++i)
        hamming[i] = docs[hamming_positions[i]];
    std::vector<float> adc_distances(hamming.size());
    for (std::size_t i = 0; i != hamming.size(); ++i) {
        const auto document = hamming[i];
        for (std::size_t bit = 0; bit != BITS; ++bit) {
            const auto symbol = (x.codes[document * BYTES + bit / 8] >>
                                 (bit % 8)) & 1U;
            const auto delta = x.qproj[qi * BITS + bit] -
                               x.adc[bit * 2 + symbol];
            adc_distances[i] += delta * delta;
        }
    }
    const auto adc_positions = top<float>(adc_distances, 64,
        [](auto a, auto b, std::uint32_t i, std::uint32_t j) {
            return a == b ? i < j : a < b;
        });
    std::vector<std::uint32_t> adc_documents(adc_positions.size());
    for (std::size_t i = 0; i != adc_documents.size(); ++i)
        adc_documents[i] = hamming[adc_positions[i]];
    std::vector<float> exact_scores(adc_documents.size());
    for (std::size_t i = 0; i != adc_documents.size(); ++i)
        exact_scores[i] = dot(x.docs.data() + adc_documents[i] * D,
                              x.queries.data() + qi * D, D);
    const auto exact_positions = top<float>(exact_scores, 10,
        [](auto a, auto b, std::uint32_t i, std::uint32_t j) {
            return a == b ? i < j : a > b;
        });
    std::vector<std::uint32_t> selected(exact_positions.size());
    for (std::size_t i = 0; i != selected.size(); ++i)
        selected[i] = adc_documents[exact_positions[i]];
    Row result;
    result.overlap = hits(selected);
    result.candidate_overlap = hits(docs);
    result.hamming_overlap = hits(hamming);
    result.adc_overlap = hits(adc_documents);
    result.qndcg = ndcg(selected, x, qi);
    result.candidates = docs.size();
    result.selected = selected;
    result.route = std::chrono::duration<double, std::milli>(
        Clock::now() - begin).count();
    result.total = result.route;
    return result;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 3 || argc > 7) {
            std::cerr << "usage: native_document_routing_bakeoff <manifest> <output> [debug] [query_limit] [repeats]\n";
            return 2;
        }
        auto input = load(argv[1]);
        const bool debug = argc == 4;
        if (debug) input.q = std::min<std::size_t>(5, input.q);
        if (argc >= 5) input.q = std::min<std::size_t>(input.q,
            static_cast<std::size_t>(std::stoul(argv[4])));
        const int requested_repeats = argc >= 6 ? std::stoi(argv[5]) : (debug ? 1 : 4);
        if (requested_repeats < 1) throw std::runtime_error("repeats must be positive");
        json output{{"schema_version", 2},
                    {"family", "native_document_routing_bakeoff_result_v1"},
                    {"protocol", {{"query_count", input.q},
                                   {"repeats", requested_repeats},
                                   {"hamming_limit", 768}, {"adc_limit", 64},
                                   {"exact_limit", 10},
                                   {"policy_matrix", "pure_and_seed_plus_pca_fallback"}}},
                    {"rows", json::array()}};
        const std::vector<std::string> policies{
            "pca_threshold", "pca_centroid_k1", "e5_centroid_k1",
            "e5_centroid_k2", "e5_centroid_k4", "e5_centroid_k8"};
        const auto emit = [&](const std::string& policy, std::size_t budget,
                              const Model* model) {
            std::vector<Row> rows;
            const int repeats = requested_repeats;
            for (std::size_t query = 0; query != input.q; ++query)
                for (int repeat = 0; repeat != repeats; ++repeat)
                    rows.push_back(run(input, policy, query, budget, model));
            double overlap = 0, candidate_overlap = 0, hamming_overlap = 0;
            double adc_overlap = 0, qrels = 0, candidates = 0;
            std::vector<double> timing;
            std::vector<double> candidate_values, hamming_values, adc_values,
                final_values, ndcg_values;
            for (const auto& row : rows) {
                overlap += row.overlap; candidate_overlap += row.candidate_overlap;
                hamming_overlap += row.hamming_overlap; adc_overlap += row.adc_overlap;
                qrels += row.qndcg; candidates += row.candidates;
                timing.push_back(row.total);
                candidate_values.push_back(row.candidate_overlap);
                hamming_values.push_back(row.hamming_overlap);
                adc_values.push_back(row.adc_overlap);
                final_values.push_back(row.overlap);
                ndcg_values.push_back(row.qndcg);
            }
            std::sort(timing.begin(), timing.end());
            output["rows"].push_back({
                {"policy", policy}, {"budget", budget}, {"query_count", input.q},
                {"mean_candidate_overlap", candidate_overlap / rows.size()},
                {"mean_hamming_overlap", hamming_overlap / rows.size()},
                {"mean_adc_overlap", adc_overlap / rows.size()},
                {"mean_overlap", overlap / rows.size()},
                {"mean_qrels_ndcg", qrels / rows.size()},
                {"p05_candidate_overlap", quantile(candidate_values, 0.05)},
                {"worst_candidate_overlap", *std::min_element(candidate_values.begin(), candidate_values.end())},
                {"p05_hamming_overlap", quantile(hamming_values, 0.05)},
                {"worst_hamming_overlap", *std::min_element(hamming_values.begin(), hamming_values.end())},
                {"p05_adc_overlap", quantile(adc_values, 0.05)},
                {"worst_adc_overlap", *std::min_element(adc_values.begin(), adc_values.end())},
                {"p05_final_overlap", quantile(final_values, 0.05)},
                {"worst_final_overlap", *std::min_element(final_values.begin(), final_values.end())},
                {"p05_qrels_ndcg", quantile(ndcg_values, 0.05)},
                {"worst_qrels_ndcg", *std::min_element(ndcg_values.begin(), ndcg_values.end())},
                {"mean_candidates", candidates / rows.size()},
                {"p95_total_ms", timing[static_cast<std::size_t>(
                    .95 * static_cast<double>(timing.size() - 1))]},
                {"first_exact_documents", rows.front().selected}});
        };
        for (const auto& policy : policies)
            for (const auto budget : {32000U, 64000U})
                emit(policy, budget, nullptr);
        for (const auto& policy : {"pca_centroid_k1_hybrid8", "e5_centroid_k1_hybrid8",
                                   "e5_centroid_k2_hybrid8", "e5_centroid_k4_hybrid8",
                                   "e5_centroid_k8_hybrid8"})
            for (const auto budget : {32000U, 64000U})
                emit(policy, budget, nullptr);
        for (std::size_t seed = 0; seed != input.models.size(); ++seed)
            for (const auto top_n : {8U, 16U, 32U, 64U})
                for (const auto budget : {32000U, 64000U}) {
                    emit("direct4096_top" + std::to_string(top_n) + "_seed" + std::to_string(seed),
                         budget, &input.models[seed]);
                    emit("direct4096_top" + std::to_string(top_n) + "_hybrid_seed" + std::to_string(seed),
                         budget, &input.models[seed]);
                }
        std::ofstream result(argv[2]);
        result << output.dump(2) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

#include "MdbxCanonicalContentStore.hpp"
#include <agent_memory/storage/CanonicalContentStoreInternal.hpp>

#if AGENT_MEMORY_HAS_MDBX
#include <mdbx_containers/KeyValueTable.hpp>
#include <cctype>
#include <mutex>
#include <limits>
#include <set>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

constexpr std::string_view REV_VERSION = "agent_memory.canonical_revision.v1";
constexpr std::string_view BODY_VERSION = "agent_memory.canonical_body.v1";
constexpr std::string_view LEDGER_VERSION = "agent_memory.canonical_ledger.v1";

std::string safe_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& c : value) if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    return value;
}
std::string key(const std::string& doc, std::uint64_t revision) {
    return doc + "\x1f" + std::to_string(revision);
}
void put_size(std::string& out, std::size_t value) { out += std::to_string(value); out.push_back(':'); }
void put_string(std::string& out, std::string_view value) { put_size(out, value.size()); out.append(value.data(), value.size()); }
void put_u64(std::string& out, std::uint64_t value) { put_string(out, std::to_string(value)); }

class Reader final {
public:
    explicit Reader(std::string_view payload) : m_payload(payload) {}
    std::size_t size() {
        if(m_pos >= m_payload.size()) throw std::runtime_error("truncated canonical payload");
        std::size_t value = 0; bool digit = false;
        while(m_pos < m_payload.size() && m_payload[m_pos] != ':') {
            const auto c = static_cast<unsigned char>(m_payload[m_pos]);
            if(c < '0' || c > '9') throw std::runtime_error("invalid canonical length");
            if(value > (static_cast<std::size_t>(-1) - (c - '0')) / 10) throw std::runtime_error("canonical length overflow");
            value = value * 10 + (c - '0'); digit = true; ++m_pos;
        }
        if(!digit || m_pos >= m_payload.size()) throw std::runtime_error("invalid canonical length marker");
        ++m_pos; return value;
    }
    std::string string() { const auto n = size(); if(n > m_payload.size() - m_pos) throw std::runtime_error("canonical payload overrun"); std::string v(m_payload.data()+m_pos,n); m_pos += n; return v; }
    std::uint64_t u64() { const auto text = string(); std::size_t used = 0; const auto v = std::stoull(text, &used); if(used != text.size()) throw std::runtime_error("invalid canonical integer"); return v; }
    void end() const { if(m_pos != m_payload.size()) throw std::runtime_error("canonical payload trailing data"); }
private: std::string_view m_payload; std::size_t m_pos = 0;
};

void metadata_put(std::string& out, const Metadata& metadata) {
    put_size(out, metadata.size());
    for(const auto& item : metadata.values()) { put_string(out, item.first); put_string(out, item.second); }
}
Metadata metadata_get(Reader& r) {
    Metadata result; const auto n = r.size();
    for(std::size_t i=0; i<n; ++i) result.set(r.string(), r.string());
    return result;
}
std::string encode_revision(const CanonicalDocumentRevision& revision, std::uint64_t body_revision) {
    std::string out; put_string(out, REV_VERSION); put_u64(out, body_revision); put_string(out, revision.document_id.value()); put_u64(out, revision.revision); metadata_put(out, revision.metadata); put_size(out, revision.blocks.size());
    for(const auto& block : revision.blocks) { put_string(out, block.id.value()); put_u64(out, block.revision); put_size(out, static_cast<std::size_t>(block.kind)); put_size(out, block.parent_id ? 1 : 0); if(block.parent_id) put_string(out, block.parent_id->value()); put_string(out, block.text); }
    return out;
}
std::pair<CanonicalDocumentRevision, std::uint64_t> decode_revision(std::string_view payload) {
    Reader r(payload); if(r.string() != REV_VERSION) throw std::runtime_error("unsupported canonical revision payload");
    const auto body_revision = r.u64(); CanonicalDocumentRevision revision{DocumentId{r.string()}, r.u64(), metadata_get(r), {}};
    const auto n = r.size(); revision.blocks.reserve(n);
    for(std::size_t i=0;i<n;++i) { ContentBlock b; b.id = ContentBlockId{r.string()}; b.revision = r.u64(); const auto kind = r.size(); if(kind > 2) throw std::runtime_error("invalid canonical block kind"); b.kind = static_cast<ContentBlockKind>(kind); const auto has_parent = r.size(); if(has_parent > 1) throw std::runtime_error("invalid canonical parent marker"); if(has_parent == 1) b.parent_id = ContentBlockId{r.string()}; b.text = r.string(); revision.blocks.push_back(std::move(b)); }
    r.end(); return {std::move(revision), body_revision};
}
std::string encode_body(const CanonicalDocumentRevision& revision, std::uint64_t body_revision) {
    std::string out; put_string(out, BODY_VERSION); put_u64(out, body_revision); put_size(out, revision.blocks.size());
    for(const auto& b : revision.blocks) { put_string(out,b.id.value()); put_size(out,static_cast<std::size_t>(b.kind)); put_size(out,b.parent_id?1:0); if(b.parent_id) put_string(out,b.parent_id->value()); put_string(out,b.text); }
    return out;
}
std::pair<std::uint64_t, std::vector<ContentBlock>> decode_body(std::string_view payload) {
    Reader r(payload); if(r.string() != BODY_VERSION) throw std::runtime_error("unsupported canonical body payload");
    const auto body_revision = r.u64(); const auto n = r.size(); std::vector<ContentBlock> blocks; blocks.reserve(n);
    for(std::size_t i = 0; i < n; ++i) { ContentBlock block; block.id = ContentBlockId{r.string()}; const auto kind = r.size(); if(kind > 2) throw std::runtime_error("invalid canonical body kind"); block.kind = static_cast<ContentBlockKind>(kind); const auto has_parent = r.size(); if(has_parent > 1) throw std::runtime_error("invalid canonical body parent marker"); if(has_parent == 1) block.parent_id = ContentBlockId{r.string()}; block.text = r.string(); blocks.push_back(std::move(block)); }
    r.end(); return {body_revision, std::move(blocks)};
}
bool body_matches(const std::vector<ContentBlock>& body,
    const CanonicalDocumentRevision& revision) {
    if(body.size() != revision.blocks.size()) return false;
    for(std::size_t i = 0; i < body.size(); ++i) {
        if(body[i].id != revision.blocks[i].id || body[i].kind != revision.blocks[i].kind ||
            body[i].parent_id != revision.blocks[i].parent_id || body[i].text != revision.blocks[i].text) return false;
    }
    return true;
}
std::string encode_ledger(const std::set<ContentBlockId>& ids) { std::string out; put_string(out,LEDGER_VERSION); put_size(out,ids.size()); for(const auto& id:ids) put_string(out,id.value()); return out; }
std::set<ContentBlockId> decode_ledger(std::string_view payload) { Reader r(payload); if(r.string()!=LEDGER_VERSION) throw std::runtime_error("unsupported canonical ledger payload"); std::set<ContentBlockId> ids; const auto n=r.size(); for(std::size_t i=0;i<n;++i) ids.insert(ContentBlockId{r.string()}); r.end(); return ids; }

} // namespace

class MdbxCanonicalContentStore::Impl final {
public:
    explicit Impl(MdbxCanonicalContentStoreOptions options)
        : context(options.context ? std::move(options.context) : MdbxStorageContext::open(std::move(options.path), options.relative_to_exe)),
          prefix(safe_part(std::move(options.table_prefix))),
          heads(context->connection(), prefix + "_canonical_heads"), revisions(context->connection(), prefix + "_canonical_revisions"), bodies(context->connection(), prefix + "_canonical_bodies"), ledgers(context->connection(), prefix + "_canonical_ledgers") {}

    struct Loaded { std::optional<CanonicalDocumentRevision> current; std::set<ContentBlockId> ledger; std::uint64_t body_revision = 0; };
    Loaded load(const DocumentId& id, const mdbxc::Transaction& txn) {
        Loaded loaded;
        const auto head = heads.find(id.value(), txn);
        if(!head) return loaded;
        const auto ledger = ledgers.find(id.value(), txn);
        if(!ledger) throw std::runtime_error("canonical head references missing ledger");
        loaded.ledger = decode_ledger(*ledger);
        const auto head_revision = std::stoull(*head);
        const auto payload = revisions.find(key(id.value(), head_revision), txn);
        if(!payload) throw std::runtime_error("canonical head references missing revision");
        auto decoded = decode_revision(*payload);
        if(decoded.first.document_id != id || decoded.first.revision != head_revision)
            throw std::runtime_error("canonical head binding mismatch");
        const auto body = bodies.find(key(id.value(), decoded.second), txn);
        if(!body) throw std::runtime_error("canonical revision references missing body");
        const auto decoded_body = decode_body(*body);
        if(decoded_body.first != decoded.second || !body_matches(decoded_body.second, decoded.first))
            throw std::runtime_error("canonical body binding mismatch");
        loaded.body_revision = decoded.second;
        loaded.current = std::move(decoded.first);
        return loaded;
    }
    void write_revision(const CanonicalDocumentRevision& revision, std::uint64_t body_revision, const std::set<ContentBlockId>& ledger, const mdbxc::Transaction& txn) {
        revisions.insert_or_assign(key(revision.document_id.value(),revision.revision), encode_revision(revision,body_revision), txn);
        bodies.insert_or_assign(key(revision.document_id.value(),body_revision), encode_body(revision,body_revision), txn);
        heads.insert_or_assign(revision.document_id.value(), std::to_string(revision.revision), txn);
        ledgers.insert_or_assign(revision.document_id.value(), encode_ledger(ledger), txn);
    }
    std::shared_ptr<MdbxStorageContext> context; std::string prefix; mutable std::mutex mutex;
    mdbxc::KeyValueTable<std::string,std::string> heads, revisions, bodies, ledgers;
};

MdbxCanonicalContentStore::MdbxCanonicalContentStore(MdbxCanonicalContentStoreOptions options):m_impl(std::make_unique<Impl>(std::move(options))){}
MdbxCanonicalContentStore::~MdbxCanonicalContentStore()=default;

bool MdbxCanonicalContentStore::create_document(CanonicalDocumentRevision initial) {
    try {
        std::lock_guard<std::mutex> lock(m_impl->mutex);
        InMemoryCanonicalContentStore validator;
        if(!validator.create_document(initial)) return false;
        initial = *validator.read_current(initial.document_id);
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        if(m_impl->heads.find(initial.document_id.value(), txn)) return false;
        std::set<ContentBlockId> ids;
        for(const auto& block : initial.blocks) ids.insert(block.id);
        m_impl->write_revision(initial, 1, ids, txn);
        txn.commit();
        return true;
    } catch(...) { return false; }
}
std::optional<CanonicalDocumentRevision> MdbxCanonicalContentStore::read_current(const DocumentId& id) const { try { auto t=m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY); const auto h=m_impl->heads.find(id.value(),t); if(!h) return std::nullopt; const auto p=m_impl->revisions.find(key(id.value(),std::stoull(*h)),t); if(!p)return std::nullopt; auto decoded=decode_revision(*p); const auto body=m_impl->bodies.find(key(id.value(),decoded.second),t); if(!body) return std::nullopt; const auto decoded_body=decode_body(*body); if(decoded_body.first != decoded.second || !body_matches(decoded_body.second,decoded.first)) return std::nullopt; return decoded.first; } catch(...) { return std::nullopt; } }
std::optional<CanonicalDocumentRevision> MdbxCanonicalContentStore::read_revision(const DocumentId& id,std::uint64_t rev) const { try { auto t=m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY); const auto p=m_impl->revisions.find(key(id.value(),rev),t); if(!p) return std::nullopt; auto decoded=decode_revision(*p); const auto body=m_impl->bodies.find(key(id.value(),decoded.second),t); if(!body) return std::nullopt; const auto decoded_body=decode_body(*body); if(decoded_body.first != decoded.second || !body_matches(decoded_body.second,decoded.first)) return std::nullopt; return decoded.first; } catch(...) { return std::nullopt; } }
std::optional<ContentBlock> MdbxCanonicalContentStore::read_block(const DocumentId& id,const ContentBlockId& bid,std::optional<std::uint64_t> rev) const { const auto state=rev?read_revision(id,*rev):read_current(id); if(!state)return std::nullopt; for(const auto& b:state->blocks) if(b.id==bid)return b; return std::nullopt; }
std::optional<std::string> MdbxCanonicalContentStore::materialize_markdown(const DocumentId& id,std::optional<std::uint64_t> rev) const { const auto state=rev?read_revision(id,*rev):read_current(id); if(!state)return std::nullopt; auto baseline=*state; baseline.revision=0; for(auto& b:baseline.blocks)b.revision=1; InMemoryCanonicalContentStore temp; if(!temp.create_document(std::move(baseline))) return std::nullopt; return temp.materialize_markdown(id); }
CanonicalEditResult MdbxCanonicalContentStore::commit(const CanonicalEditRequest& request) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    try {
        auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        auto loaded = m_impl->load(request.document_id, txn);
        if(!loaded.current) {
            txn.rollback(); CanonicalEditResult result; result.status = CanonicalEditStatus::NotFound; result.message = "document not found"; return result;
        }
        InMemoryCanonicalContentStore temp;
        const auto previous = *loaded.current;
        if(!detail::CanonicalContentHistoryLoader::restore_current(
            temp, std::move(*loaded.current), loaded.ledger)) {
            txn.rollback(); CanonicalEditResult result; result.status = CanonicalEditStatus::InvalidEdit; result.message = "corrupt canonical history"; return result;
        }
        auto result = temp.commit(request);
        if(!result.succeeded()) { txn.rollback(); return result; }
        const auto& revision = *result.revision;
        if(loaded.body_revision == 0) {
            txn.rollback(); CanonicalEditResult invalid; invalid.status = CanonicalEditStatus::InvalidEdit; invalid.message = "missing logical body binding"; return invalid;
        }
        std::uint64_t body_revision = loaded.body_revision;
        bool body_changed = previous.blocks.size() != revision.blocks.size();
        if(!body_changed) {
            for(std::size_t i = 0; i < revision.blocks.size(); ++i) {
                const auto& left = previous.blocks[i]; const auto& right = revision.blocks[i];
                if(left.id != right.id || left.kind != right.kind || left.parent_id != right.parent_id || left.text != right.text) { body_changed = true; break; }
            }
        }
        if(body_changed) ++body_revision;
        std::set<ContentBlockId> ledger = loaded.ledger;
        for(const auto& block : revision.blocks) ledger.insert(block.id);
        m_impl->write_revision(revision, body_revision, ledger, txn);
        txn.commit();
        return result;
    } catch(...) {
        CanonicalEditResult result; result.status = CanonicalEditStatus::InvalidEdit; result.message = "canonical payload or transaction failure"; return result;
    }
}

} // namespace agent_memory
#endif

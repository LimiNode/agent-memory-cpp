#include "MdbxCanonicalFlatStorage.hpp"

#if AGENT_MEMORY_HAS_MDBX

#include "MdbxStorageIdentityMapping.hpp"

#include <agent_memory/storage/CanonicalFlatStorage.hpp>
#include <mdbx_containers/KeyValueTable.hpp>

#include <algorithm>
#include <cctype>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace agent_memory {
namespace {

using storage::CanonicalFlatBlockRecord;
using storage::CanonicalFlatBlockRef;
using storage::CanonicalFlatStorageCodec;
using storage::CanonicalFlatStorageLimits;
using storage::CanonicalFlatStructureRecord;

constexpr std::string_view kSchemaKey = "@schema_version";
constexpr std::string_view kSchemaVersion = "canonical_flat_storage.v1";

std::string sanitize_table_part(std::string value) {
    if(value.empty()) value = "agent_memory";
    for(char& c : value) {
        if(!std::isalnum(static_cast<unsigned char>(c))) c = '_';
    }
    return value;
}

std::string table_name(const std::string& prefix, std::string_view suffix) {
    auto result = sanitize_table_part(prefix);
    result.push_back('_');
    result.append(suffix.data(), suffix.size());
    return result;
}

std::string revision_key(std::uint64_t document_id, std::uint64_t revision) {
    return std::to_string(document_id) + "/" + std::to_string(revision);
}

std::string block_key(std::uint64_t block_id, std::uint64_t revision) {
    return std::to_string(block_id) + "/" + std::to_string(revision);
}

std::uint8_t encode_kind(ContentBlockKind kind) {
    switch(kind) {
    case ContentBlockKind::Heading: return 0;
    case ContentBlockKind::Paragraph: return 1;
    case ContentBlockKind::Code: return 2;
    }
    throw std::invalid_argument("unknown canonical block kind");
}

ContentBlockKind decode_kind(std::uint8_t kind) {
    switch(kind) {
    case 0: return ContentBlockKind::Heading;
    case 1: return ContentBlockKind::Paragraph;
    case 2: return ContentBlockKind::Code;
    default: throw std::runtime_error("invalid canonical flat block kind");
    }
}

CanonicalFlatBlockRecord to_flat_block(const ContentBlock& block) {
    CanonicalFlatBlockRecord result;
    result.id = block.id.value();
    result.revision = block.revision;
    result.kind = encode_kind(block.kind);
    if(block.parent_id) result.parent_id = block.parent_id->value();
    result.text = block.text;
    return result;
}

CanonicalFlatBlockRef to_flat_ref(const ContentBlock& block) {
    CanonicalFlatBlockRef result;
    result.id = block.id.value();
    result.revision = block.revision;
    result.kind = encode_kind(block.kind);
    if(block.parent_id) result.parent_id = block.parent_id->value();
    return result;
}

CanonicalFlatStructureRecord to_flat_structure(const CanonicalDocumentRevision& revision) {
    CanonicalFlatStructureRecord result;
    result.document_id = revision.document_id.value();
    result.document_revision = revision.revision;
    result.body_revision = revision.revision;
    for(const auto& item : revision.metadata.values()) {
        result.metadata.emplace_back(item.first, item.second);
    }
    result.blocks.reserve(revision.blocks.size());
    for(const auto& block : revision.blocks) result.blocks.push_back(to_flat_ref(block));
    return result;
}

Metadata from_metadata(const CanonicalFlatStructureRecord& structure) {
    Metadata result;
    for(const auto& item : structure.metadata) result.set(item.first, item.second);
    return result;
}

bool has_any_rows(mdbxc::KeyValueTable<std::string, std::string>& table,
                  const mdbxc::Transaction& txn) {
    bool present = false;
    table.for_each_range(
        std::string{}, std::string(1, static_cast<char>(0xff)),
        [&present](const std::string&, const std::string&) {
            present = true;
            return false;
        }, txn);
    return present;
}

bool has_any_rows(mdbxc::KeyValueTable<std::uint64_t, std::string>& table,
                  const mdbxc::Transaction& txn) {
    bool present = false;
    table.for_each_range(
        0U, std::numeric_limits<std::uint64_t>::max(),
        [&present](std::uint64_t, const std::string&) {
            present = true;
            return false;
        }, txn);
    return present;
}

} // namespace

class MdbxCanonicalFlatStorage::Impl final {
  public:
    explicit Impl(MdbxCanonicalFlatStorageOptions options)
        : max_records(options.max_records),
          context(options.context ? std::move(options.context)
                                  : MdbxStorageContext::open(std::move(options.path),
                                                             options.relative_to_exe,
                                                             options.max_dbs)),
          identity([&] {
              MdbxStorageIdentityMappingOptions identity_options;
              identity_options.context = context;
              identity_options.table_prefix = options.table_prefix;
              identity_options.max_dbs = options.max_dbs;
              identity_options.max_records = options.max_records;
              return identity_options;
          }()),
          marker(context->connection(), table_name(options.table_prefix, "canonical_flat_meta_v1")),
          heads(context->connection(), table_name(options.table_prefix, "canonical_flat_heads_v1")),
          structures(context->connection(), table_name(options.table_prefix, "canonical_flat_structures_v1")),
          blocks(context->connection(), table_name(options.table_prefix, "canonical_flat_blocks_v1")) {
        if(max_records == 0U) throw std::invalid_argument("max_records must be positive");
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        const auto version = marker.find(std::string{kSchemaKey}, txn);
        if(version) {
            if(*version != kSchemaVersion) {
                txn.rollback();
                throw std::runtime_error("unsupported canonical flat storage schema");
            }
        } else {
            if(has_any_rows(marker, txn) || has_any_rows(heads, txn) ||
               has_any_rows(structures, txn) || has_any_rows(blocks, txn)) {
                txn.rollback();
                throw std::runtime_error("canonical flat storage rejects markerless state");
            }
            marker.insert_or_assign(std::string{kSchemaKey}, std::string{kSchemaVersion}, txn);
        }
        txn.commit();
    }

    std::optional<CanonicalDocumentRevision> load_revision(
        const DocumentId& document_id, std::uint64_t revision) const {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto document_storage_id = identity.find_document_in_transaction(document_id, txn);
        if(!document_storage_id) {
            txn.commit();
            return std::nullopt;
        }
        const auto encoded_structure = structures.find(
            revision_key(document_storage_id->value(), revision), txn);
        if(!encoded_structure) {
            txn.commit();
            return std::nullopt;
        }
        const auto structure = CanonicalFlatStorageCodec::decode_structure(
            *encoded_structure, limits);
        if(structure.document_id != document_id.value() ||
           structure.document_revision != revision) {
            txn.rollback();
            throw std::runtime_error("canonical flat structure identity mismatch");
        }
        CanonicalDocumentRevision result{
            document_id, structure.document_revision, from_metadata(structure), {}};
        result.blocks.reserve(structure.blocks.size());
        for(const auto& ref : structure.blocks) {
            const auto block_storage_id = identity.find_block_in_transaction(
                document_id, ContentBlockId{ref.id}, txn);
            if(!block_storage_id) {
                txn.rollback();
                throw std::runtime_error("canonical flat structure references missing block mapping");
            }
            const auto payload = blocks.find(
                block_key(block_storage_id->value(), ref.revision), txn);
            if(!payload) {
                txn.rollback();
                throw std::runtime_error("canonical flat structure references missing block row");
            }
            const auto flat = CanonicalFlatStorageCodec::decode_block(*payload, limits);
            if(flat.id != ref.id || flat.revision != ref.revision || flat.kind != ref.kind ||
               flat.parent_id != ref.parent_id) {
                txn.rollback();
                throw std::runtime_error("canonical flat structure/block binding mismatch");
            }
            ContentBlock block{
                ContentBlockId{flat.id}, flat.revision, decode_kind(flat.kind),
                flat.parent_id ? std::optional<ContentBlockId>{ContentBlockId{*flat.parent_id}}
                               : std::nullopt,
                flat.text};
            result.blocks.push_back(std::move(block));
        }
        txn.commit();
        return result;
    }

    std::optional<std::uint64_t> current_revision(const DocumentId& document_id) const {
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        const auto document_storage_id = identity.find_document_in_transaction(document_id, txn);
        if(!document_storage_id) {
            txn.commit();
            return std::nullopt;
        }
        const auto value = heads.find(document_storage_id->value(), txn);
        if(!value) {
            txn.commit();
            return std::nullopt;
        }
        try {
            std::size_t consumed = 0;
            const auto result = std::stoull(*value, &consumed);
            if(consumed != value->size())
                throw std::runtime_error("invalid canonical flat head revision");
            txn.commit();
            return result;
        } catch(const std::exception&) {
            txn.rollback();
            throw std::runtime_error("invalid canonical flat head revision");
        }
    }

    void write_revision_locked(const CanonicalDocumentRevision& revision) {
        if(revision.blocks.size() > max_records)
            throw std::length_error("canonical flat block count exceeds max_records");
        const auto flat_structure = to_flat_structure(revision);
        const auto structure_payload = CanonicalFlatStorageCodec::encode_structure(
            flat_structure, limits);
        auto txn = context->connection()->transaction(mdbxc::TransactionMode::WRITABLE);
        try {
            const auto document_storage_id = identity.ensure_document_in_transaction(
                revision.document_id, txn);
            for(const auto& block : revision.blocks) {
                const auto block_storage_id = identity.ensure_block_in_transaction(
                    revision.document_id, block.id, txn);
                blocks.insert_or_assign(
                    block_key(block_storage_id.value(), block.revision),
                    CanonicalFlatStorageCodec::encode_block(to_flat_block(block), limits), txn);
            }
            structures.insert_or_assign(
                revision_key(document_storage_id.value(), revision.revision),
                structure_payload, txn);
            heads.insert_or_assign(
                document_storage_id.value(), std::to_string(revision.revision), txn);
            txn.commit();
        } catch(...) {
            txn.rollback();
            throw;
        }
    }

    void write_revision(const CanonicalDocumentRevision& revision) {
        std::lock_guard<std::mutex> lock(mutex);
        write_revision_locked(revision);
    }

    const std::size_t max_records;
    const CanonicalFlatStorageLimits limits{};
    std::shared_ptr<MdbxStorageContext> context;
    mutable std::mutex mutex;
    mutable MdbxStorageIdentityMapping identity;
    mutable mdbxc::KeyValueTable<std::string, std::string> marker;
    mutable mdbxc::KeyValueTable<std::uint64_t, std::string> heads;
    mutable mdbxc::KeyValueTable<std::string, std::string> structures;
    mutable mdbxc::KeyValueTable<std::string, std::string> blocks;
};

MdbxCanonicalFlatStorage::MdbxCanonicalFlatStorage(MdbxCanonicalFlatStorageOptions options)
    : m_impl(std::make_unique<Impl>(std::move(options))) {}

MdbxCanonicalFlatStorage::~MdbxCanonicalFlatStorage() = default;

bool MdbxCanonicalFlatStorage::create_document(CanonicalDocumentRevision initial) {
    if(initial.document_id.empty() || initial.revision != 0U) return false;
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    if(m_impl->identity.find_document(initial.document_id)) return false;
    InMemoryCanonicalContentStore validator;
    if(!validator.create_document(initial)) return false;
    initial = *validator.read_current(initial.document_id);
    m_impl->write_revision_locked(initial);
    return true;
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalFlatStorage::read_current(const DocumentId& document_id) const {
    const auto revision = m_impl->current_revision(document_id);
    if(!revision) return std::nullopt;
    return m_impl->load_revision(document_id, *revision);
}

std::optional<CanonicalDocumentRevision>
MdbxCanonicalFlatStorage::read_revision(const DocumentId& document_id,
                                        std::uint64_t revision) const {
    return m_impl->load_revision(document_id, revision);
}

std::optional<ContentBlock> MdbxCanonicalFlatStorage::read_block(
    const DocumentId& document_id, const ContentBlockId& block_id,
    std::optional<std::uint64_t> revision) const {
    auto txn = m_impl->context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
    const auto document_storage_id = m_impl->identity.find_document_in_transaction(
        document_id, txn);
    if(!document_storage_id) {
        txn.commit();
        return std::nullopt;
    }
    std::optional<std::uint64_t> selected_revision = revision;
    if(!selected_revision) {
        const auto value = m_impl->heads.find(document_storage_id->value(), txn);
        if(!value) {
            txn.commit();
            return std::nullopt;
        }
        try {
            std::size_t consumed = 0;
            const auto parsed = std::stoull(*value, &consumed);
            if(consumed != value->size())
                throw std::runtime_error("invalid canonical flat head revision");
            selected_revision = parsed;
        } catch(const std::exception&) {
            txn.rollback();
            throw std::runtime_error("invalid canonical flat head revision");
        }
    }
    const auto structure_payload = m_impl->structures.find(
        revision_key(document_storage_id->value(), *selected_revision), txn);
    if(!structure_payload) {
        txn.commit();
        return std::nullopt;
    }
    const auto structure = CanonicalFlatStorageCodec::decode_structure(
        *structure_payload, m_impl->limits);
    const auto ref = std::find_if(
        structure.blocks.begin(), structure.blocks.end(),
        [&block_id](const CanonicalFlatBlockRef& value) { return value.id == block_id.value(); });
    if(ref == structure.blocks.end()) {
        txn.commit();
        return std::nullopt;
    }
    const auto block_storage_id = m_impl->identity.find_block_in_transaction(
        document_id, block_id, txn);
    if(!block_storage_id) {
        txn.rollback();
        throw std::runtime_error("canonical flat block mapping is missing");
    }
    const auto payload = m_impl->blocks.find(
        block_key(block_storage_id->value(), ref->revision), txn);
    if(!payload) {
        txn.rollback();
        throw std::runtime_error("canonical flat block row is missing");
    }
    const auto flat = CanonicalFlatStorageCodec::decode_block(*payload, m_impl->limits);
    txn.commit();
    return ContentBlock{
        ContentBlockId{flat.id}, flat.revision, decode_kind(flat.kind),
        flat.parent_id ? std::optional<ContentBlockId>{ContentBlockId{*flat.parent_id}}
                       : std::nullopt,
        flat.text};
}

std::optional<std::string> MdbxCanonicalFlatStorage::materialize_markdown(
    const DocumentId& document_id, std::optional<std::uint64_t> revision) const {
    const auto value = revision ? read_revision(document_id, *revision)
                                : read_current(document_id);
    if(!value) return std::nullopt;
    InMemoryCanonicalContentStore renderer;
    if(!renderer.create_document(*value))
        throw std::runtime_error("canonical flat renderer rejected stored revision");
    return renderer.materialize_markdown(document_id, value->revision);
}

CanonicalEditResult MdbxCanonicalFlatStorage::commit(const CanonicalEditRequest& request) {
    std::lock_guard<std::mutex> lock(m_impl->mutex);
    const auto current = m_impl->current_revision(request.document_id);
    if(!current) {
        CanonicalEditResult result;
        result.status = CanonicalEditStatus::NotFound;
        result.message = "canonical flat document was not found";
        return result;
    }
    const auto stored = m_impl->load_revision(request.document_id, *current);
    if(!stored) throw std::runtime_error("canonical flat head row is missing");
    InMemoryCanonicalContentStore editor;
    if(!editor.create_document(*stored))
        throw std::runtime_error("canonical flat editor rejected stored revision");
    const auto result = editor.commit(request);
    if(result.status == CanonicalEditStatus::Ok && result.revision)
        m_impl->write_revision_locked(*result.revision);
    return result;
}

} // namespace agent_memory

#endif // AGENT_MEMORY_HAS_MDBX

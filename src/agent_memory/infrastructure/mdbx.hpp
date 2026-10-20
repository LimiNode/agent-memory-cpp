#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_HPP_INCLUDED

/// \file mdbx.hpp
/// \brief Aggregate include for optional MDBX-backed infrastructure adapters.

#include "mdbx/MdbxCanonicalContentStore.hpp"
#include "mdbx/MdbxCanonicalContentRouter.hpp"
#include "mdbx/MdbxFederatedCanonicalReader.hpp"
#include "mdbx/MdbxDocumentStorage.hpp"
#include "mdbx/MdbxResourceManifestStorage.hpp"
#include "mdbx/MdbxStorageContext.hpp"
#include "mdbx/MdbxWorkspaceStorageRegistry.hpp"
#include "mdbx/MdbxDurableLexicalDocumentMemory.hpp"
#include "mdbx/MdbxResourceImporter.hpp"
#include "mdbx/MdbxKnowledgeUnitStore.hpp"
#include "mdbx/MdbxProjectionStore.hpp"

#endif

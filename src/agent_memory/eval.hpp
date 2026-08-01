#pragma once
#ifndef AGENT_MEMORY_HEADER_EVAL_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_EVAL_HPP_INCLUDED

/// \file eval.hpp
/// \brief Public aggregate include for retrieval evaluation domain headers.

#include "domain.hpp"
#include "eval/BenchmarkReport.hpp"
#include "eval/AutoencoderBinaryEvaluation.hpp"
#include "eval/Evaluation.hpp"
#include "eval/IRetrieverAdapter.hpp"
#include "eval/RetrievalEvalRunner.hpp"

#if defined(AGENT_MEMORY_ENABLE_JSON) && AGENT_MEMORY_ENABLE_JSON
#include "eval/AutoencoderBinaryArtifact.hpp"
#include "eval/DatasetLoader.hpp"
#include "eval/PrecomputedEmbeddingDataset.hpp"
#endif

#endif

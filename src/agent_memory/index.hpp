#pragma once
#ifndef AGENT_MEMORY_HEADER_INDEX_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INDEX_HPP_INCLUDED

/// \file index.hpp
/// \brief Public aggregate include for vector index domain headers.

#include "domain.hpp"
#include "embedding.hpp"

#include "index/AggregateBinarySignature.hpp"
#include "index/AutoencoderBinaryEncoder.hpp"
#include "index/BinarySignature.hpp"
#include "index/BinarySignatureInfo.hpp"
#include "index/BinarySignatureEncoderRegistry.hpp"
#include "index/BinarySignatureIndex.hpp"
#include "index/CoordinateSignBinaryEncoder.hpp"
#include "index/ExactVectorIndex.hpp"
#include "index/FlatBinarySignatureIndex.hpp"
#include "index/IBinarySignatureEncoder.hpp"
#include "index/IBinarySignatureIndex.hpp"
#include "index/ItqRotationBinaryEncoder.hpp"
#include "index/IVectorIndex.hpp"
#include "index/LearnedProjectionBinaryEncoder.hpp"
#include "index/MultiProbeHammingIndex.hpp"
#include "index/PcaProjectionBinaryEncoder.hpp"
#include "index/RandomHyperplaneBinaryEncoder.hpp"
#include "index/RandomizedHadamardBinaryEncoder.hpp"
#include "index/VectorSimilarityComputer.hpp"
#include "index/VectorIndex.hpp"

#endif

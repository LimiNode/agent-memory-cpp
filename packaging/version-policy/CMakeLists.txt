cmake_minimum_required(VERSION 3.20)
project(agent_memory_version_policy_consumer LANGUAGES NONE)

if(NOT DEFINED AGENT_MEMORY_REQUESTED_VERSION)
    message(FATAL_ERROR "AGENT_MEMORY_REQUESTED_VERSION is required")
endif()
if(NOT DEFINED AGENT_MEMORY_EXPECTED_FOUND)
    message(FATAL_ERROR "AGENT_MEMORY_EXPECTED_FOUND is required")
endif()

find_package(agent_memory ${AGENT_MEMORY_REQUESTED_VERSION} CONFIG QUIET)

message(STATUS "agent_memory effective CMAKE_PREFIX_PATH=${CMAKE_PREFIX_PATH}")
message(STATUS "agent_memory_DIR=${agent_memory_DIR}")
message(STATUS "agent_memory_CONSIDERED_CONFIGS=${agent_memory_CONSIDERED_CONFIGS}")
message(STATUS "agent_memory_CONSIDERED_VERSIONS=${agent_memory_CONSIDERED_VERSIONS}")
message(STATUS "agent_memory_FOUND=${agent_memory_FOUND}")
message(STATUS "CMAKE_SYSTEM_NAME=${CMAKE_SYSTEM_NAME}")
message(STATUS "CMAKE_SIZEOF_VOID_P=${CMAKE_SIZEOF_VOID_P}")

if(DEFINED agent_memory_DIR)
    set(_agent_memory_config
        "${agent_memory_DIR}/agent_memoryConfig.cmake")
    set(_agent_memory_version_config
        "${agent_memory_DIR}/agent_memoryConfigVersion.cmake")
    message(STATUS "agent_memoryConfig.cmake=${_agent_memory_config}")
    message(STATUS "agent_memoryConfigVersion.cmake=${_agent_memory_version_config}")
    if(EXISTS "${_agent_memory_version_config}")
        file(STRINGS "${_agent_memory_version_config}" _version_excerpt
            LIMIT_COUNT 12)
        string(REPLACE ";" "\n" _version_excerpt_text "${_version_excerpt}")
        message(STATUS
            "agent_memoryConfigVersion.cmake excerpt:\n${_version_excerpt_text}")
    endif()
endif()

if(AGENT_MEMORY_EXPECTED_FOUND)
    if(NOT agent_memory_FOUND)
        message(FATAL_ERROR
            "agent_memory ${AGENT_MEMORY_REQUESTED_VERSION} was expected to be compatible")
    endif()
else()
    if(agent_memory_FOUND)
        message(FATAL_ERROR
            "agent_memory ${AGENT_MEMORY_REQUESTED_VERSION} was unexpectedly compatible")
    endif()
endif()

message(STATUS
    "agent_memory ${AGENT_MEMORY_REQUESTED_VERSION}: expected=${AGENT_MEMORY_EXPECTED_FOUND}, observed=${agent_memory_FOUND}")

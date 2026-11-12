cmake_minimum_required(VERSION 3.20)
project(agent_memory_version_policy_consumer LANGUAGES NONE)

if(NOT DEFINED AGENT_MEMORY_REQUESTED_VERSION)
    message(FATAL_ERROR "AGENT_MEMORY_REQUESTED_VERSION is required")
endif()
if(NOT DEFINED AGENT_MEMORY_EXPECTED_FOUND)
    message(FATAL_ERROR "AGENT_MEMORY_EXPECTED_FOUND is required")
endif()

find_package(agent_memory ${AGENT_MEMORY_REQUESTED_VERSION} CONFIG QUIET)

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

cmake_minimum_required(VERSION 3.20)

foreach(_required IN ITEMS
    AGENT_MEMORY_CONSUMER_SOURCE_DIR
    AGENT_MEMORY_CONSUMER_BUILD_DIR
    AGENT_MEMORY_CONSUMER_MODE
    AGENT_MEMORY_EXPECTED_EXECUTABLE)
    if(NOT DEFINED ${_required})
        message(FATAL_ERROR "${_required} is required")
    endif()
endforeach()

if(NOT DEFINED CMAKE_COMMAND)
    message(FATAL_ERROR "CMAKE_COMMAND is required")
endif()

set(_configure_args
    -S "${AGENT_MEMORY_CONSUMER_SOURCE_DIR}"
    -B "${AGENT_MEMORY_CONSUMER_BUILD_DIR}"
    -DCMAKE_BUILD_TYPE=Release
)

if(DEFINED AGENT_MEMORY_SOURCE_DIR)
    list(APPEND _configure_args
        -DAGENT_MEMORY_SOURCE_DIR=${AGENT_MEMORY_SOURCE_DIR})
endif()
if(DEFINED CMAKE_PREFIX_PATH)
    list(APPEND _configure_args
        -DCMAKE_PREFIX_PATH=${CMAKE_PREFIX_PATH})
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" ${_configure_args}
    RESULT_VARIABLE _configure_result
)
if(NOT _configure_result EQUAL 0)
    message(FATAL_ERROR "consumer configure failed: ${_configure_result}")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" --build "${AGENT_MEMORY_CONSUMER_BUILD_DIR}"
        --config Release
    RESULT_VARIABLE _build_result
)
if(NOT _build_result EQUAL 0)
    message(FATAL_ERROR "consumer build failed: ${_build_result}")
endif()

file(GLOB_RECURSE _consumer_binaries
    "${AGENT_MEMORY_CONSUMER_BUILD_DIR}/${AGENT_MEMORY_EXPECTED_EXECUTABLE}"
    "${AGENT_MEMORY_CONSUMER_BUILD_DIR}/${AGENT_MEMORY_EXPECTED_EXECUTABLE}.exe"
    "${AGENT_MEMORY_CONSUMER_BUILD_DIR}/Release/${AGENT_MEMORY_EXPECTED_EXECUTABLE}"
    "${AGENT_MEMORY_CONSUMER_BUILD_DIR}/Release/${AGENT_MEMORY_EXPECTED_EXECUTABLE}.exe"
)
list(LENGTH _consumer_binaries _consumer_binary_count)
if(_consumer_binary_count EQUAL 0)
    message(FATAL_ERROR "consumer executable was not produced")
endif()
list(GET _consumer_binaries 0 _consumer_binary)

execute_process(
    COMMAND "${_consumer_binary}"
    WORKING_DIRECTORY "${AGENT_MEMORY_CONSUMER_BUILD_DIR}"
    RESULT_VARIABLE _run_result
)
if(NOT _run_result EQUAL 0)
    message(FATAL_ERROR "consumer execution failed: ${_run_result}")
endif()

message(STATUS "consumer ${AGENT_MEMORY_CONSUMER_MODE}: PASS")

cmake_minimum_required(VERSION 3.20)

foreach(_required IN ITEMS
    AGENT_MEMORY_SOURCE_DIR
    AGENT_MEMORY_HEADER_FIXTURE_BUILD_ROOT)
    if(NOT DEFINED ${_required} OR "${${_required}}" STREQUAL "")
        message(FATAL_ERROR "${_required} is required")
    endif()
endforeach()

set(_generator_args)
if(DEFINED AGENT_MEMORY_HEADER_FIXTURE_GENERATOR)
    list(APPEND _generator_args -G "${AGENT_MEMORY_HEADER_FIXTURE_GENERATOR}")
endif()

foreach(_forbidden IN ITEMS OFF ON)
    execute_process(
        COMMAND "${CMAKE_COMMAND}"
            -S "${CMAKE_CURRENT_LIST_DIR}"
            -B "${AGENT_MEMORY_HEADER_FIXTURE_BUILD_ROOT}/${_forbidden}"
            ${_generator_args}
            "-DAGENT_MEMORY_SOURCE_DIR=${AGENT_MEMORY_SOURCE_DIR}"
            "-DAGENT_MEMORY_FIXTURE_WITH_FORBIDDEN_HEADER=${_forbidden}"
        RESULT_VARIABLE _result
        OUTPUT_VARIABLE _stdout
        ERROR_VARIABLE _stderr
        TIMEOUT 30
    )
    # Keep diagnostic text separate from stdout and visible on every path.
    message(STATUS "header-manifest fixture forbidden=${_forbidden}, result=${_result}\n${_stdout}\n${_stderr}")
    if(NOT "${_result}" MATCHES "^[0-9]+$")
        message(FATAL_ERROR "header-manifest fixture did not complete normally")
    endif()
    if(NOT _forbidden)
        if(NOT _result EQUAL 0)
            message(FATAL_ERROR "nested header-manifest fixture failed")
        endif()
    else()
        if(_result EQUAL 0)
            message(FATAL_ERROR "negative header-manifest fixture unexpectedly passed")
        endif()
        if(NOT _stderr MATCHES
            "backend-only CanonicalContentStoreInternal\\.hpp leaked into the installed[ \t\r\n]+package")
            message(FATAL_ERROR "negative header-manifest fixture failed without the expected forbidden-header diagnostic")
        endif()
    endif()
endforeach()

message(STATUS "agent_memory header-manifest fixtures: PASS")

cmake_minimum_required(VERSION 3.20)

if(NOT DEFINED AGENT_MEMORY_PACKAGE_PREFIX)
    message(FATAL_ERROR "AGENT_MEMORY_PACKAGE_PREFIX is required")
endif()
if(NOT DEFINED AGENT_MEMORY_VERSION_POLICY_SOURCE_DIR)
    message(FATAL_ERROR "AGENT_MEMORY_VERSION_POLICY_SOURCE_DIR is required")
endif()
if(NOT DEFINED AGENT_MEMORY_VERSION_POLICY_BUILD_ROOT)
    message(FATAL_ERROR "AGENT_MEMORY_VERSION_POLICY_BUILD_ROOT is required")
endif()

set(_versions 0.1.0 0.1.1 0.2.0)
set(_expected TRUE FALSE FALSE)
list(LENGTH _versions _count)
math(EXPR _last "${_count} - 1")

foreach(_index RANGE ${_last})
    list(GET _versions ${_index} _version)
    list(GET _expected ${_index} _should_be_found)
    set(_build_dir
        "${AGENT_MEMORY_VERSION_POLICY_BUILD_ROOT}/request-${_version}")
    file(REMOVE_RECURSE "${_build_dir}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}"
            -S "${AGENT_MEMORY_VERSION_POLICY_SOURCE_DIR}"
            -B "${_build_dir}"
            "-DCMAKE_PREFIX_PATH=${AGENT_MEMORY_PACKAGE_PREFIX}"
            "-DAGENT_MEMORY_REQUESTED_VERSION=${_version}"
            "-DAGENT_MEMORY_EXPECTED_FOUND=${_should_be_found}"
        RESULT_VARIABLE _result
        OUTPUT_VARIABLE _stdout
        ERROR_VARIABLE _stderr
    )
    if(NOT _result EQUAL 0)
        message(FATAL_ERROR
            "version request ${_version} failed (expected=${_should_be_found})\n"
            "${_stdout}\n${_stderr}")
    endif()
    message(STATUS
        "version request ${_version}: expected=${_should_be_found}, result=PASS")
    if(_stdout)
        message(STATUS "${_stdout}")
    endif()
    if(_stderr)
        message(STATUS "${_stderr}")
    endif()
endforeach()

message(STATUS "agent_memory package version policy: PASS")

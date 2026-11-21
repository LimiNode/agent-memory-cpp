# Dependency helpers for agent-memory-cpp.

set(_AGENT_MEMORY_DEPENDENCIES_DIR "${CMAKE_CURRENT_LIST_DIR}")

function(_agent_memory_has_mdbx_target out_has_target)
    set(${out_has_target} FALSE PARENT_SCOPE)

    foreach(_target
        mdbx::mdbx
        mdbx::mdbx-static
        mdbx
        mdbx-static
        libmdbx::mdbx
        libmdbx::mdbx-static
        libmdbx
        libmdbx-static
        mdbx_static
        libmdbx_static
    )
        if(TARGET ${_target})
            set(${out_has_target} TRUE PARENT_SCOPE)
            return()
        endif()
    endforeach()
endfunction()

function(_agent_memory_try_flat_libmdbx agent_memory_root)
    if(AGENT_MEMORY_MDBX_DEPS_MODE STREQUAL "SYSTEM")
        return()
    endif()

    _agent_memory_has_mdbx_target(_has_mdbx_target)
    if(_has_mdbx_target)
        return()
    endif()

    set(_libmdbx_source_dir "${agent_memory_root}/external/libmdbx")
    if(NOT EXISTS "${_libmdbx_source_dir}/CMakeLists.txt")
        return()
    endif()

    if(WIN32)
        get_filename_component(_libmdbx_source_dir "${_libmdbx_source_dir}" REALPATH)
    endif()

    find_program(_git_executable git)
    if(_git_executable)
        execute_process(
            COMMAND "${_git_executable}" rev-parse --is-inside-work-tree
            WORKING_DIRECTORY "${_libmdbx_source_dir}"
            OUTPUT_VARIABLE _libmdbx_is_git_repo
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET
        )
        if(_libmdbx_is_git_repo STREQUAL "true")
            execute_process(
                COMMAND "${_git_executable}" fetch --tags --force
                WORKING_DIRECTORY "${_libmdbx_source_dir}"
                RESULT_VARIABLE _libmdbx_fetch_tags_result
                OUTPUT_QUIET
                ERROR_QUIET
            )
            if(NOT _libmdbx_fetch_tags_result EQUAL 0)
                message(WARNING "[agent-memory] Failed to fetch tags for external/libmdbx")
            endif()
        endif()
    endif()

    set(MDBX_BUILD_SHARED_LIBRARY OFF CACHE BOOL "Build libmdbx shared library" FORCE)
    set(MDBX_BUILD_TOOLS OFF CACHE BOOL "Build libmdbx tools" FORCE)
    set(MDBX_ENABLE_TESTS OFF CACHE BOOL "Build libmdbx tests" FORCE)

    message(STATUS "[agent-memory] Adding flat dependency: external/libmdbx")
    add_subdirectory(
        "${_libmdbx_source_dir}"
        "${CMAKE_BINARY_DIR}/_deps/libmdbx"
        EXCLUDE_FROM_ALL
    )
    if(MSVC AND TARGET mdbx-static)
        # libmdbx's C++ translation unit checks the actual language level.
        target_compile_options(mdbx-static PRIVATE
            "$<$<COMPILE_LANGUAGE:CXX>:/Zc:__cplusplus>")
    endif()
endfunction()

function(agent_memory_provide_mdbx_containers out_target)
    if(NOT AGENT_MEMORY_MDBX_DEPS_MODE MATCHES "^(AUTO|SYSTEM|BUNDLED)$")
        message(FATAL_ERROR
            "AGENT_MEMORY_MDBX_DEPS_MODE must be AUTO, SYSTEM, or BUNDLED; "
            "got '${AGENT_MEMORY_MDBX_DEPS_MODE}'"
        )
    endif()

    if(TARGET mdbx_containers::mdbx_containers)
        set(${out_target} mdbx_containers::mdbx_containers PARENT_SCOPE)
        return()
    endif()

    get_filename_component(_agent_memory_root "${_AGENT_MEMORY_DEPENDENCIES_DIR}/.." ABSOLUTE)
    set(_mdbx_containers_source_dir "${AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR}")

    if("${_mdbx_containers_source_dir}" STREQUAL "")
        set(_default_source_dir "${_agent_memory_root}/external/mdbx-containers")
        if(EXISTS "${_default_source_dir}/CMakeLists.txt")
            set(_mdbx_containers_source_dir "${_default_source_dir}")
        endif()
    endif()

    if(NOT "${_mdbx_containers_source_dir}" STREQUAL "")
        if(NOT EXISTS "${_mdbx_containers_source_dir}/CMakeLists.txt")
            message(FATAL_ERROR
                "AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR does not contain CMakeLists.txt: "
                "${_mdbx_containers_source_dir}"
            )
        endif()

        _agent_memory_try_flat_libmdbx("${_agent_memory_root}")

        if(NOT DEFINED MDBXC_BUILD_TESTS)
            set(MDBXC_BUILD_TESTS OFF CACHE BOOL "Build mdbx-containers tests")
        endif()
        if(NOT DEFINED MDBXC_BUILD_EXAMPLES)
            set(MDBXC_BUILD_EXAMPLES OFF CACHE BOOL "Build mdbx-containers examples")
        endif()
        if(NOT DEFINED MDBXC_USE_ASAN)
            set(MDBXC_USE_ASAN OFF CACHE BOOL "Enable mdbx-containers AddressSanitizer")
        endif()
        if(NOT DEFINED MDBXC_DEPS_MODE)
            set(MDBXC_DEPS_MODE "${AGENT_MEMORY_MDBX_DEPS_MODE}" CACHE STRING
                "Dependency mode for MDBX: AUTO|SYSTEM|BUNDLED")
            set_property(CACHE MDBXC_DEPS_MODE PROPERTY STRINGS AUTO SYSTEM BUNDLED)
        endif()

        add_subdirectory(
            "${_mdbx_containers_source_dir}"
            "${CMAKE_BINARY_DIR}/_deps/mdbx-containers"
            EXCLUDE_FROM_ALL
        )
    else()
        find_package(mdbx_containers CONFIG REQUIRED)
    endif()

    if(NOT TARGET mdbx_containers::mdbx_containers)
        message(FATAL_ERROR
            "mdbx-containers was found, but target mdbx_containers::mdbx_containers is missing"
        )
    endif()

    set(${out_target} mdbx_containers::mdbx_containers PARENT_SCOPE)
endfunction()

if(NOT TARGET agent_memory::agent_memory)
    message(FATAL_ERROR
        "cannot verify installed headers without agent_memory::agent_memory")
endif()

get_target_property(
    _agent_memory_include_dirs
    agent_memory::agent_memory
    INTERFACE_INCLUDE_DIRECTORIES
)
if(
    NOT _agent_memory_include_dirs OR
    _agent_memory_include_dirs STREQUAL "_agent_memory_include_dirs-NOTFOUND"
)
    message(FATAL_ERROR
        "agent_memory::agent_memory has no resolvable installed include directories")
endif()

set(_agent_memory_checked_include_dir FALSE)
foreach(_agent_memory_include_dir IN LISTS _agent_memory_include_dirs)
    if(_agent_memory_include_dir MATCHES "\\$<")
        message(FATAL_ERROR
            "unresolved generator expression in installed include directory: "
            "${_agent_memory_include_dir}")
    endif()
    get_filename_component(
        _agent_memory_include_dir_abs
        "${_agent_memory_include_dir}"
        ABSOLUTE
    )
    if(NOT IS_DIRECTORY "${_agent_memory_include_dir_abs}")
        message(FATAL_ERROR
            "installed include directory does not exist: "
            "${_agent_memory_include_dir_abs}")
    endif()
    set(_agent_memory_checked_include_dir TRUE)

    set(_agent_memory_internal_header
        "${_agent_memory_include_dir_abs}/agent_memory/storage/CanonicalContentStoreInternal.hpp")
    if(EXISTS "${_agent_memory_internal_header}")
        message(FATAL_ERROR
            "backend-only CanonicalContentStoreInternal.hpp leaked into the installed package: "
            "${_agent_memory_internal_header}")
    endif()
endforeach()

if(NOT _agent_memory_checked_include_dir)
    message(FATAL_ERROR
        "agent_memory::agent_memory exposed no installed include directory")
endif()

unset(_agent_memory_checked_include_dir)
unset(_agent_memory_internal_header)
unset(_agent_memory_include_dir_abs)
unset(_agent_memory_include_dir)
unset(_agent_memory_include_dirs)

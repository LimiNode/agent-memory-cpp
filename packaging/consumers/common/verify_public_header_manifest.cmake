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

    foreach(_agent_memory_internal_relative_header IN ITEMS
        "agent_memory/storage/CanonicalContentStoreInternal.hpp"
        "agent_memory/storage/ConversationStoreInternal.hpp"
        "agent_memory/ingestion/ConversationSourceImporterInternal.hpp"
        "agent_memory/infrastructure/mdbx/ConversationDurableCodecInternal.hpp"
        "agent_memory/infrastructure/mdbx/MdbxConversationDurableInternal.hpp")
        set(_agent_memory_internal_header
            "${_agent_memory_include_dir_abs}/${_agent_memory_internal_relative_header}")
        if(EXISTS "${_agent_memory_internal_header}")
            get_filename_component(_agent_memory_internal_name "${_agent_memory_internal_header}" NAME)
            message(FATAL_ERROR
                "backend-only ${_agent_memory_internal_name} leaked into the installed package: "
                "${_agent_memory_internal_header}")
        endif()
    endforeach()
endforeach()

if(NOT _agent_memory_checked_include_dir)
    message(FATAL_ERROR
        "agent_memory::agent_memory exposed no installed include directory")
endif()

unset(_agent_memory_checked_include_dir)
unset(_agent_memory_internal_header)
unset(_agent_memory_internal_relative_header)
unset(_agent_memory_internal_name)
unset(_agent_memory_include_dir_abs)
unset(_agent_memory_include_dir)
unset(_agent_memory_include_dirs)

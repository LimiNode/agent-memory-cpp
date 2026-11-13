get_filename_component(_agent_memory_package_dir "${agent_memory_DIR}" ABSOLUTE)
get_filename_component(_agent_memory_install_prefix
    "${_agent_memory_package_dir}/../../.." ABSOLUTE)

set(_agent_memory_internal_header
    "${_agent_memory_install_prefix}/include/agent_memory/storage/CanonicalContentStoreInternal.hpp")
if(EXISTS "${_agent_memory_internal_header}")
    message(FATAL_ERROR
        "backend-only CanonicalContentStoreInternal.hpp leaked into the installed package: "
        "${_agent_memory_internal_header}")
endif()

unset(_agent_memory_internal_header)
unset(_agent_memory_install_prefix)
unset(_agent_memory_package_dir)

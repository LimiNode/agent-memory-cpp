cmake_minimum_required(VERSION 3.20)
project(agent_memory_mdbx_provider LANGUAGES C CXX)

if(NOT DEFINED MDBX_SOURCE_DIR OR NOT EXISTS "${MDBX_SOURCE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "MDBX_SOURCE_DIR must point to a libmdbx source tree")
endif()
set(_AGENT_MEMORY_MDBX_SOURCE_DIR "${MDBX_SOURCE_DIR}")

set(MDBX_BUILD_SHARED_LIBRARY OFF CACHE BOOL "" FORCE)
set(MDBX_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(MDBX_ENABLE_TESTS OFF CACHE BOOL "" FORCE)
set(MDBX_BUILD_CXX ON CACHE BOOL "" FORCE)
add_subdirectory("${_AGENT_MEMORY_MDBX_SOURCE_DIR}" mdbx-build EXCLUDE_FROM_ALL)
set_property(TARGET mdbx-static PROPERTY PUBLIC_HEADER "")

include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

install(TARGETS mdbx-static
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
)
install(FILES
    "${_AGENT_MEMORY_MDBX_SOURCE_DIR}/mdbx.h"
    "${_AGENT_MEMORY_MDBX_SOURCE_DIR}/mdbx.h++"
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)

set(_MDBX_PACKAGE_INSTALL_DIR "${CMAKE_INSTALL_LIBDIR}/cmake/mdbx")
configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/mdbxConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/mdbxConfig.cmake"
    INSTALL_DESTINATION "${_MDBX_PACKAGE_INSTALL_DIR}"
    NO_CHECK_REQUIRED_COMPONENTS_MACRO
)
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/mdbxConfigVersion.cmake"
    VERSION 0.14.2
    COMPATIBILITY SameMajorVersion
)
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/mdbxConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/mdbxConfigVersion.cmake"
    DESTINATION "${_MDBX_PACKAGE_INSTALL_DIR}"
)

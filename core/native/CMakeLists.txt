cmake_minimum_required(VERSION 3.24)
project(QuickFileCopyNative VERSION 1.0.0 LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT WIN32)
  message(FATAL_ERROR "QuickFileCopy native is Windows-only")
endif()

set(WEBVIEW2_INCLUDE "C:/tools/webview2/build/native/include" CACHE PATH "WebView2 SDK include directory")
set(QFC_ICON_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/QuickFileCopy.ico")
set(QFC_HTML_PATH "${CMAKE_CURRENT_SOURCE_DIR}/resources/QuickFileCopy.html")
configure_file(
  resources/QuickFileCopy.rc.in
  "${CMAKE_CURRENT_BINARY_DIR}/QuickFileCopy.rc"
  @ONLY
)

add_library(qfc_core STATIC src/copy_engine.cpp)
target_include_directories(qfc_core PUBLIC include)
target_compile_definitions(qfc_core PUBLIC UNICODE _UNICODE WINVER=0x0A00 _WIN32_WINNT=0x0A00)

add_executable(QuickFileCopy_cli src/main_cli.cpp)
target_link_libraries(QuickFileCopy_cli PRIVATE qfc_core kernel32 advapi32 bcrypt)
if(MINGW)
  target_link_options(QuickFileCopy_cli PRIVATE -municode)
endif()

add_executable(QuickFileCopy WIN32
  src/webview_main.cpp
  "${CMAKE_CURRENT_BINARY_DIR}/QuickFileCopy.rc"
)
target_include_directories(QuickFileCopy PRIVATE "${WEBVIEW2_INCLUDE}" resources)
target_compile_definitions(QuickFileCopy PRIVATE UNICODE _UNICODE WINVER=0x0A00 _WIN32_WINNT=0x0A00)
target_link_libraries(QuickFileCopy PRIVATE qfc_core kernel32 user32 dwmapi ole32 uuid shell32 advapi32 bcrypt)

if(MINGW)
  foreach(target QuickFileCopy_cli QuickFileCopy)
    target_compile_options(${target} PRIVATE -O3 -ffunction-sections -fdata-sections)
    target_link_options(${target} PRIVATE -static -static-libgcc -static-libstdc++ -Wl,--gc-sections)
  endforeach()
endif()

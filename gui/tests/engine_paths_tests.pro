QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = engine_paths_tests

include(../app/EnginePaths.pri)
SOURCES += tst_enginepaths.cpp ../settings/Settings.cpp
HEADERS += ../settings/Settings.h

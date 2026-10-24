QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = backend_process_tests

INCLUDEPATH += ..

include(../app/EnginePaths.pri)

SOURCES += \
    tst_backendprocess.cpp \
    ../backend/BackendProcess.cpp

HEADERS += \
    ../backend/BackendEvent.h \
    ../backend/BackendFailure.h \
    ../backend/BackendProcess.h \
    ../backend/TranscribeOptions.h \
    ../backend/TranscriptSegment.h

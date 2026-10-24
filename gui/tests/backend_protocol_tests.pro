QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = backend_protocol_tests

INCLUDEPATH += ..

# Python and Qt contract tests read the same JSON Lines fixtures.
DEFINES += BACKEND_PROTOCOL_FIXTURE_DIR=\\\"$$clean_path($$PWD/../../engine/tests/fixtures/backend_protocol)\\\"

SOURCES += \
    tst_backend_protocol.cpp

HEADERS += \
    ../backend/BackendEvent.h \
    ../backend/TranscriptSegment.h

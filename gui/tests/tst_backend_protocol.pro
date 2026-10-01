QT += core testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_backend_protocol

INCLUDEPATH += ..

SOURCES += \
    tst_backend_protocol.cpp

HEADERS += \
    ../backend/BackendEvent.h \
    ../backend/TranscriptSegment.h \
    ../backend/TranscribeOptions.h

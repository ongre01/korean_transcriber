QT += core testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_backend_process

INCLUDEPATH += ..

SOURCES += \
    tst_backend_process.cpp \
    ../backend/BackendProcess.cpp

HEADERS += \
    ../backend/BackendEvent.h \
    ../backend/BackendProcess.h \
    ../backend/TranscriptSegment.h \
    ../backend/TranscribeOptions.h

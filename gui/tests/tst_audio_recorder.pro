QT += core multimedia testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_audio_recorder

INCLUDEPATH += ..

SOURCES += \
    tst_audio_recorder.cpp \
    ../audio/AudioRecorder.cpp \
    ../audio/WavWriter.cpp

HEADERS += \
    ../audio/AudioRecorder.h \
    ../audio/WavWriter.h

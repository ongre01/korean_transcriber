QT += core multimedia testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_wav_writer

INCLUDEPATH += ..

SOURCES += \
    tst_wav_writer.cpp \
    ../audio/WavWriter.cpp

HEADERS += \
    ../audio/WavWriter.h

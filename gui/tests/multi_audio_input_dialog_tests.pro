QT += core gui widgets testlib
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = multi_audio_input_dialog_tests

include(../app/EnginePaths.pri)

SOURCES += \
    tst_multiaudioinputdialog.cpp \
    ../input/AudioFileInfo.cpp \
    ../input/MultiAudioInputDialog.cpp \
    ../input/MultiAudioInputModel.cpp

HEADERS += \
    ../input/AudioFileInfo.h \
    ../input/MultiAudioInputDialog.h \
    ../input/MultiAudioInputModel.h

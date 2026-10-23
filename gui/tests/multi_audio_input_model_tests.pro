QT += core testlib
QT -= gui
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = multi_audio_input_model_tests

SOURCES += \
    tst_multiaudioinputmodel.cpp \
    ../input/MultiAudioInputModel.cpp

HEADERS += \
    ../input/MultiAudioInputModel.h

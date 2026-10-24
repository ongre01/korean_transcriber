QT += core gui testlib
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = multi_audio_selection_model_tests

SOURCES += \
    tst_multiaudioselectionmodel.cpp \
    ../input/MultiAudioInputModel.cpp \
    ../input/MultiAudioSelectionModel.cpp

HEADERS += \
    ../input/MultiAudioInputModel.h \
    ../input/MultiAudioSelectionModel.h

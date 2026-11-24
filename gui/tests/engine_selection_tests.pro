QT += core gui widgets multimedia testlib
CONFIG += c++17 console testcase
CONFIG -= app_bundle
TEMPLATE = app
TARGET = engine_selection_tests

include(../app/EnginePaths.pri)

SOURCES += \
    tst_engineselection.cpp \
    ../mainwindow.cpp \
    ../audio/AudioRecorder.cpp \
    ../audio/WavWriter.cpp \
    ../backend/BackendProcess.cpp \
    ../input/AudioFileInfo.cpp \
    ../input/MultiAudioManifestFile.cpp \
    ../input/MultiAudioInputDialog.cpp \
    ../input/MultiAudioInputModel.cpp \
    ../input/MultiAudioSelectionModel.cpp \
    ../logging/AppLogger.cpp \
    ../logging/LogDialog.cpp \
    ../settings/Settings.cpp \
    ../settings/SettingsDialog.cpp

HEADERS += \
    ../mainwindow.h \
    ../audio/AudioRecorder.h \
    ../audio/WavWriter.h \
    ../backend/BackendProcess.h \
    ../input/AudioFileInfo.h \
    ../input/MultiAudioInputDialog.h \
    ../input/MultiAudioInputModel.h \
    ../input/MultiAudioSelectionModel.h \
    ../logging/LogDialog.h \
    ../settings/SettingsDialog.h

FORMS += ../mainwindow.ui

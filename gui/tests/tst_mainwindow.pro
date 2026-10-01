QT += core gui widgets multimedia testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_mainwindow

INCLUDEPATH += ..

SOURCES += \
    tst_mainwindow.cpp \
    ../mainwindow.cpp \
    ../audio/AudioRecorder.cpp \
    ../audio/WavWriter.cpp \
    ../backend/BackendProcess.cpp \
    ../input/AudioFileInfo.cpp

HEADERS += \
    ../mainwindow.h \
    ../app/AppState.h \
    ../audio/AudioRecorder.h \
    ../audio/WavWriter.h \
    ../backend/BackendEvent.h \
    ../backend/BackendProcess.h \
    ../backend/TranscriptSegment.h \
    ../backend/TranscribeOptions.h \
    ../input/AudioFileInfo.h

FORMS += \
    ../mainwindow.ui

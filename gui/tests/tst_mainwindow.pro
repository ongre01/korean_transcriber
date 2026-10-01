QT += core gui widgets testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_mainwindow

INCLUDEPATH += ..

SOURCES += \
    tst_mainwindow.cpp \
    ../mainwindow.cpp \
    ../backend/BackendProcess.cpp \
    ../input/AudioFileInfo.cpp

HEADERS += \
    ../mainwindow.h \
    ../app/AppState.h \
    ../backend/BackendEvent.h \
    ../backend/BackendProcess.h \
    ../backend/TranscriptSegment.h \
    ../backend/TranscribeOptions.h \
    ../input/AudioFileInfo.h

FORMS += \
    ../mainwindow.ui

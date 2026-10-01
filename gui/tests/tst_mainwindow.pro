QT += core gui widgets testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_mainwindow

INCLUDEPATH += ..

SOURCES += \
    tst_mainwindow.cpp \
    ../mainwindow.cpp \
    ../input/AudioFileInfo.cpp

HEADERS += \
    ../mainwindow.h \
    ../app/AppState.h \
    ../input/AudioFileInfo.h

FORMS += \
    ../mainwindow.ui

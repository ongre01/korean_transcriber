QT += core testlib

CONFIG += c++17 console testcase
TEMPLATE = app
TARGET = tst_audio_file_info

INCLUDEPATH += ..

SOURCES += \
    tst_audio_file_info.cpp \
    ../input/AudioFileInfo.cpp

HEADERS += \
    ../input/AudioFileInfo.h

RESOURCES += \
    ../resources/resources.qrc

# TICKET-001 verified target environment:
#   Windows 10 Pro x64
#   Qt 6.11.0, MSVC 2022 64-bit
# Qt 6 recording code must use QAudioSource. The Qt 5 fallback, if a Qt 5
# build is added later, must use QAudioInput.

QT += core gui widgets multimedia

CONFIG += c++17

TEMPLATE = app
TARGET = AudioTranscriber

greaterThan(QT_MAJOR_VERSION, 5) {
    DEFINES += AUDIOTRANSCRIBER_USE_QAUDIOSOURCE
    message("Audio capture API: QAudioSource (Qt $$[QT_VERSION])")
} else {
    DEFINES += AUDIOTRANSCRIBER_USE_QAUDIOINPUT
    message("Audio capture API: QAudioInput (Qt $$[QT_VERSION])")
}

SOURCES += \
    main.cpp \
    mainwindow.cpp \
    backend/BackendProcess.cpp

HEADERS += \
    mainwindow.h \
    app/AppState.h \
    backend/BackendEvent.h \
    backend/BackendProcess.h \
    backend/TranscriptSegment.h \
    backend/TranscribeOptions.h

FORMS += \
    mainwindow.ui

# Default rules for deployment.
qnx: target.path = /tmp/$${TARGET}/bin
else: unix:!android: target.path = /opt/$${TARGET}/bin
!isEmpty(target.path): INSTALLS += target

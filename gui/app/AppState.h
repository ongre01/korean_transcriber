#ifndef APPSTATE_H
#define APPSTATE_H

#include <QMetaType>

enum class AppState
{
    Idle,
    Recording,
    InputReady,
    Processing,
    Completed,
    Error
};

Q_DECLARE_METATYPE(AppState)

struct AppActionPolicy
{
    bool canStartRecording;
    bool canSelectFile;
    bool canStopRecording;
    bool canStartTranscription;
    bool canCancel;
};

constexpr AppActionPolicy appActionPolicy(AppState state, bool hasValidInput)
{
    switch (state) {
    case AppState::Idle:
        return {true, true, false, false, false};
    case AppState::Recording:
        return {false, false, true, false, false};
    case AppState::InputReady:
        return {true, true, false, hasValidInput, false};
    case AppState::Processing:
        return {false, false, false, false, true};
    case AppState::Completed:
    case AppState::Error:
        return {true, true, false, hasValidInput, false};
    }

    return {false, false, false, false, false};
}

#endif // APPSTATE_H

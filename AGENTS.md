# AGENTS.md

## Development Guide

이 프로젝트를 수정하거나 기능을 구현하기 전에 아래 개발 사양서를 먼저 읽고 내용을 준수한다.

- [Qt Widgets 기반 음성 녹음·전사 UI 개발 사양서](./docs/Qt_Widgets_음성_녹음_전사_UI_개발_사양서.md)

## 기본 개발 환경

- Qt Widgets
- qmake
- C++17
- Qt Multimedia
- Python Backend 연동: `QProcess`

## 구현 원칙

- UI 및 녹음 기능은 Qt/C++에서 구현한다.
- Whisper, OpenVINO, 화자 분리 등 기존 AI 처리 기능은 Python 코드를 재사용한다.
- 기존 Python 처리 로직을 불필요하게 C++로 재구현하지 않는다.
- 개발 사양서와 구현 내용이 충돌할 경우 개발 사양서를 우선한다.
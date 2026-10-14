ENGINE_SOURCE_DIRECTORY = $$clean_path($$PWD/../../engine)
engine_paths_config.input = $$PWD/EnginePathsConfig.h.in
engine_paths_config.output = $$OUT_PWD/EnginePathsConfig.h
QMAKE_SUBSTITUTES += engine_paths_config
INCLUDEPATH += $$OUT_PWD

SOURCES += $$PWD/EnginePaths.cpp
HEADERS += $$PWD/EnginePaths.h

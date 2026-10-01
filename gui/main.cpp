#include "mainwindow.h"

#include <QApplication>
#include <QCoreApplication>

int main(int argc, char *argv[])
{
    QApplication a(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("KoreanTranscriber"));
    QCoreApplication::setApplicationName(QStringLiteral("AudioTranscriber"));
    MainWindow w;
    w.show();
    return QApplication::exec();
}

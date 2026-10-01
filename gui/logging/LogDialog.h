#ifndef LOGDIALOG_H
#define LOGDIALOG_H

#include <QDialog>
#include <QString>

class QComboBox;
class QLabel;
class QPlainTextEdit;

class LogDialog final : public QDialog
{
public:
    explicit LogDialog(QString logDirectory, QWidget *parent = nullptr);

private:
    void refreshFiles();
    void loadSelectedFile(int index);

    QString m_logDirectory;
    QComboBox *m_fileComboBox = nullptr;
    QLabel *m_pathLabel = nullptr;
    QPlainTextEdit *m_logTextEdit = nullptr;
};

#endif // LOGDIALOG_H

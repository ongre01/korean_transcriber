#include "LogDialog.h"

#include <QComboBox>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSignalBlocker>
#include <QTextCursor>
#include <QVBoxLayout>

LogDialog::LogDialog(QString logDirectory, QWidget *parent)
    : QDialog(parent)
    , m_logDirectory(QDir::cleanPath(logDirectory))
{
    setWindowTitle(tr("상세 로그"));
    resize(840, 560);

    auto *layout = new QVBoxLayout(this);
    auto *selectionLayout = new QHBoxLayout;
    selectionLayout->addWidget(new QLabel(tr("로그 날짜:"), this));

    m_fileComboBox = new QComboBox(this);
    selectionLayout->addWidget(m_fileComboBox, 1);

    auto *refreshButton = new QPushButton(tr("새로 고침"), this);
    selectionLayout->addWidget(refreshButton);
    layout->addLayout(selectionLayout);

    m_pathLabel = new QLabel(this);
    m_pathLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    m_pathLabel->setWordWrap(true);
    layout->addWidget(m_pathLabel);

    m_logTextEdit = new QPlainTextEdit(this);
    m_logTextEdit->setReadOnly(true);
    m_logTextEdit->setLineWrapMode(QPlainTextEdit::NoWrap);
    layout->addWidget(m_logTextEdit, 1);

    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, this);
    layout->addWidget(buttons);

    connect(m_fileComboBox, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &LogDialog::loadSelectedFile);
    connect(refreshButton, &QPushButton::clicked, this, &LogDialog::refreshFiles);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);

    refreshFiles();
}

void LogDialog::refreshFiles()
{
    const QString currentPath = m_fileComboBox->currentData().toString();
    const QSignalBlocker blocker(m_fileComboBox);
    m_fileComboBox->clear();

    const QDir directory(m_logDirectory);
    const QFileInfoList files = directory.entryInfoList(
        {QStringLiteral("app_*.log")}, QDir::Files | QDir::Readable, QDir::Name | QDir::Reversed);
    int selectedIndex = -1;
    for (const QFileInfo &file : files) {
        m_fileComboBox->addItem(file.fileName(), file.absoluteFilePath());
        if (file.absoluteFilePath() == currentPath) {
            selectedIndex = m_fileComboBox->count() - 1;
        }
    }
    if (selectedIndex >= 0) {
        m_fileComboBox->setCurrentIndex(selectedIndex);
    }

    if (m_fileComboBox->count() == 0) {
        m_fileComboBox->setEnabled(false);
        m_pathLabel->setText(tr("로그 폴더: %1").arg(QDir::toNativeSeparators(m_logDirectory)));
        m_logTextEdit->setPlainText(tr("표시할 로그 파일이 없습니다."));
        return;
    }

    m_fileComboBox->setEnabled(true);
    loadSelectedFile(m_fileComboBox->currentIndex());
}

void LogDialog::loadSelectedFile(int index)
{
    if (index < 0) {
        return;
    }

    const QString path = m_fileComboBox->itemData(index).toString();
    m_pathLabel->setText(tr("로그 파일: %1").arg(QDir::toNativeSeparators(path)));

    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        m_logTextEdit->setPlainText(
            tr("로그 파일을 읽을 수 없습니다: %1").arg(file.errorString()));
        return;
    }

    m_logTextEdit->setPlainText(QString::fromUtf8(file.readAll()));
    m_logTextEdit->moveCursor(QTextCursor::End);
}

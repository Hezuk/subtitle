"""에러 분류 및 사용자 메시지 매핑."""


class CancelledError(Exception):
    """사용자가 작업을 취소했을 때 발생."""
    pass


class SubtitleError(Exception):
    """모든 자막 앱 예외의 기반 클래스."""
    user_message = "알 수 없는 오류가 발생했습니다."

    def __init__(self, detail: str = "", user_message: str | None = None):
        self.detail = detail
        if user_message:
            self.user_message = user_message
        super().__init__(detail or self.user_message)


class ConfigError(SubtitleError):
    user_message = "서버 설정 오류 — 관리자에게 문의하세요."


class UploadError(SubtitleError):
    user_message = "파일 업로드에 실패했습니다."


class TranscriptionError(SubtitleError):
    user_message = "음성 인식에 실패했습니다. 파일이 올바른 영상인지 확인해주세요."


class TranslationError(SubtitleError):
    user_message = "번역에 실패했습니다. 잠시 후 다시 시도해주세요."


class ReviewError(SubtitleError):
    user_message = "번역 검토에 실패했습니다. 잠시 후 다시 시도해주세요."


class EncodeError(SubtitleError):
    user_message = "인코딩에 실패했습니다. 자막을 확인한 후 다시 시도해주세요."

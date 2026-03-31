"""에러 분류 및 사용자 메시지 매핑."""


class CancelledError(Exception):
    """사용자가 작업을 취소했을 때 발생."""
    pass


class SubtitleError(Exception):
    """모든 자막 앱 예외의 기반 클래스."""
    user_message = "알 수 없는 오류가 발생했습니다."
    error_code = "unknown_error"
    hint = ""
    retryable = False

    def __init__(
        self,
        detail: str = "",
        user_message: str | None = None,
        hint: str | None = None,
        error_code: str | None = None,
        retryable: bool | None = None,
    ):
        self.detail = detail
        if user_message:
            self.user_message = user_message
        if hint is not None:
            self.hint = hint
        if error_code:
            self.error_code = error_code
        if retryable is not None:
            self.retryable = retryable
        super().__init__(detail or self.user_message)


class ConfigError(SubtitleError):
    user_message = "서버 설정 오류 — 관리자에게 문의하세요."
    error_code = "config_error"


class UploadError(SubtitleError):
    user_message = "파일 업로드에 실패했습니다."
    error_code = "upload_error"


class TranscriptionError(SubtitleError):
    user_message = "음성 인식에 실패했습니다. 파일이 올바른 영상인지 확인해주세요."
    error_code = "transcription_error"


class TranslationError(SubtitleError):
    user_message = "번역에 실패했습니다. 잠시 후 다시 시도해주세요."
    error_code = "translation_error"


class ReviewError(SubtitleError):
    user_message = "번역 검토에 실패했습니다. 잠시 후 다시 시도해주세요."
    error_code = "review_error"


class EncodeError(SubtitleError):
    user_message = "인코딩에 실패했습니다. 자막을 확인한 후 다시 시도해주세요."
    error_code = "encode_error"

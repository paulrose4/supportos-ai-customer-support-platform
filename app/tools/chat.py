from app.application.dto import HandleChatCommand, HandleChatResult
from app.application.services import HandleChatService


class ChatTool:
    def __init__(self, service: HandleChatService) -> None:
        self._service = service

    async def invoke(self, command: HandleChatCommand) -> HandleChatResult:
        return await self._service.execute(command)

"""
用户提问工具模块
提供向用户提问并获取答案的功能
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union
import json

from .base import Tool, ToolResult, ToolError, ToolExecutionError, ToolValidationError, register_tool


@dataclass
class QuestionOption:
    """问题选项数据结构"""
    value: str
    label: Optional[str] = None


@dataclass
class Question:
    """问题数据结构"""
    question: str
    options: Optional[List[Union[str, Dict[str, str]]]] = None
    allow_multiple: bool = False
    required: bool = True


@dataclass
class AskUserQuestionInput:
    """向用户提问工具的输入参数"""
    questions: List[Dict[str, Any]]  # 问题列表，每项包含 question, options 等


@dataclass
class UserAnswer:
    """用户答案数据结构"""
    question: str
    answer: Union[str, List[str], None]
    question_index: int


@register_tool
class AskUserQuestionTool(Tool[AskUserQuestionInput, List[UserAnswer]]):
    """
    向用户提问工具

    向用户展示问题列表并获取答案
    支持单选、多选和自由文本回答
    返回用户的答案列表

    注意：这是一个交互式工具，实际使用时需要与前端/UI集成
    在当前实现中，返回模拟答案用于演示和测试
    """

    name = "ask_user_question"
    description = "向用户提问并获取答案，支持单选、多选和文本回答"
    version = "1.0"

    async def validate(self, input_data: AskUserQuestionInput) -> Optional[ToolError]:
        if not input_data.questions:
            return ToolValidationError("questions 不能为空列表")

        # 验证每个问题的结构
        for i, q in enumerate(input_data.questions):
            if not isinstance(q, dict):
                return ToolValidationError(f"问题[{i}] 必须是字典类型")

            if "question" not in q:
                return ToolValidationError(f"问题[{i}] 缺少 'question' 字段")

            if not q["question"] or not str(q["question"]).strip():
                return ToolValidationError(f"问题[{i}] 的 question 不能为空")

            # 验证选项（如果提供）
            options = q.get("options")
            if options is not None:
                if not isinstance(options, list):
                    return ToolValidationError(f"问题[{i}] 的 options 必须是列表类型")

                for j, opt in enumerate(options):
                    if isinstance(opt, dict):
                        if "value" not in opt:
                            return ToolValidationError(f"问题[{i}] 的选项[{j}] 缺少 'value' 字段")
                    elif not isinstance(opt, str):
                        return ToolValidationError(
                            f"问题[{i}] 的选项[{j}] 必须是字符串或字典类型"
                        )

        return None

    async def execute(self, input_data: AskUserQuestionInput) -> ToolResult:
        try:
            questions = input_data.questions
            answers: List[Dict[str, Any]] = []

            for i, q_data in enumerate(questions):
                question_text = str(q_data["question"]).strip()
                options = q_data.get("options")
                allow_multiple = q_data.get("allow_multiple", False)
                required = q_data.get("required", True)

                # 处理选项
                processed_options = None
                if options:
                    processed_options = []
                    for opt in options:
                        if isinstance(opt, dict):
                            processed_options.append({
                                "value": opt["value"],
                                "label": opt.get("label", opt["value"]),
                            })
                        else:
                            processed_options.append({
                                "value": str(opt),
                                "label": str(opt),
                            })

                # 构建问题对象
                question_obj = {
                    "question": question_text,
                    "options": processed_options,
                    "allow_multiple": allow_multiple,
                    "required": required,
                    "index": i,
                }

                # 在实际应用中，这里应该暂停执行并等待用户输入
                # 在模拟实现中，我们根据问题类型生成默认答案
                answer = self._generate_default_answer(question_obj)

                answers.append({
                    "question": question_text,
                    "answer": answer,
                    "question_index": i,
                    "options": processed_options,
                    "allow_multiple": allow_multiple,
                })

            return ToolResult.ok(
                data={
                    "answers": answers,
                    "question_count": len(questions),
                },
                message=f"成功获取用户对 {len(questions)} 个问题的回答",
                metadata={
                    "question_count": len(questions),
                    "answers_count": len(answers),
                }
            )

        except Exception as e:
            return ToolResult.error(
                ToolExecutionError(f"提问过程失败: {str(e)}")
            )

    def _generate_default_answer(self, question: Dict[str, Any]) -> Union[str, List[str], None]:
        """
        生成默认答案（用于演示/测试）

        在实际应用中，这个方法应该被替换为真正的用户交互
        """
        options = question.get("options")
        allow_multiple = question.get("allow_multiple", False)
        required = question.get("required", True)

        if not required:
            # 非必填问题可以返回 None
            return None

        if options:
            if allow_multiple:
                # 多选：返回第一个选项
                return [options[0]["value"]] if options else []
            else:
                # 单选：返回第一个选项
                return options[0]["value"] if options else None
        else:
            # 文本问题：返回占位符
            return "[用户输入]"

    def is_read_only(self) -> bool:
        """是否为只读工具"""
        return True

    def requires_confirmation(self) -> bool:
        """是否需要用户确认"""
        return False

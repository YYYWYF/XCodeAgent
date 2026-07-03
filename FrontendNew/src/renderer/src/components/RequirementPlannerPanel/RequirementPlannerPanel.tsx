import {
  CheckCircleOutlined,
  OrderedListOutlined,
  QuestionCircleOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Input,
  Radio,
  Space,
  Spin,
  Steps,
  Tag,
  Typography,
} from 'antd';
import { useMemo, useRef, useState } from 'react';
import {
  RequirementPlannerSession,
  type RequirementAnswer,
  type RequirementPlannerPayload,
  type RequirementPlannerState,
  type RequirementQuestion,
} from '../../service/agUiAgent';
import type { ApplicationConfig, RequirementDevelopmentPlan } from '../../typings';
import { cx } from '../../utils';
import MarkdownContent from '../MarkdownContent/MarkdownContent';
import './RequirementPlannerPanel.less';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

type DraftAnswerValue = string | string[];
type DraftAnswers = Record<string, DraftAnswerValue | undefined>;

type Props = {
  application: ApplicationConfig;
  onPlanChange: (plan: RequirementDevelopmentPlan) => void;
};

export default function RequirementPlannerPanel({ application, onPlanChange }: Props) {
  const sessionRef = useRef<RequirementPlannerSession | null>(null);
  const [requirement, setRequirement] = useState('');
  const [plannerState, setPlannerState] = useState<RequirementPlannerState | undefined>(() =>
    application.requirementPlan
      ? {
          requirement: '',
          answers: [],
          iteration: 0,
          status: 'plan',
          plan: application.requirementPlan,
        }
      : undefined,
  );
  const [questions, setQuestions] = useState<RequirementQuestion[]>([]);
  const [draftAnswers, setDraftAnswers] = useState<DraftAnswers>({});
  const [agentMessage, setAgentMessage] = useState('');
  const [plan, setPlan] = useState<RequirementDevelopmentPlan | undefined>(
    application.requirementPlan,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();

  const collectedAnswerCount = plannerState?.answers.length ?? 0;
  const stepIndex = plan ? 2 : questions.length > 0 ? 1 : 0;
  const canFinalize = collectedAnswerCount > 0 && !loading;
  const requirementPlaceholder = useMemo(
    () =>
      `例如：我要做一个${application.name}，核心用户是业务运营人员，需要管理客户、查看数据、处理审批。`,
    [application.name],
  );

  const sendPlannerMessage = async (
    message: string,
    action: 'start' | 'answer' | 'finalize',
    state?: RequirementPlannerState,
  ) => {
    setLoading(true);
    setError(undefined);
    try {
      const session =
        sessionRef.current ??
        (sessionRef.current = new RequirementPlannerSession());
      const result = await session.sendMessage(message, {
        action,
        plannerState: state,
        application,
      });
      if (!result.planning) {
        throw new Error('规划 agent 没有返回结构化 planning 数据。');
      }
      applyPlanningPayload(result.planning, result.answer);
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : '调用需求规划 agent 失败。');
    } finally {
      setLoading(false);
    }
  };

  const handleStart = () => {
    const trimmedRequirement = requirement.trim();
    if (!trimmedRequirement) {
      setError('请先输入一段初始需求。');
      return;
    }
    setPlan(undefined);
    setQuestions([]);
    setDraftAnswers({});
    const initialState: RequirementPlannerState = {
      requirement: trimmedRequirement,
      answers: [],
      iteration: 0,
    };
    setPlannerState(initialState);
    sendPlannerMessage(trimmedRequirement, 'start', initialState);
  };

  const handleSubmitAnswers = () => {
    const missingQuestion = questions.find((question) => {
      const value = draftAnswers[question.id];
      return question.required && (Array.isArray(value) ? value.length === 0 : !value);
    });
    if (missingQuestion) {
      setError(`请先回答「${missingQuestion.title}」。`);
      return;
    }

    const answers = questions
      .map((question): RequirementAnswer | null => {
        const value = draftAnswers[question.id];
        if (Array.isArray(value) && value.length === 0) return null;
        if (!Array.isArray(value) && !value) return null;
        return {
          questionId: question.id,
          question: question.title,
          value: value ?? '',
          label: formatAnswerLabel(question, value),
        };
      })
      .filter((answer): answer is RequirementAnswer => Boolean(answer));
    const nextState: RequirementPlannerState = {
      requirement: plannerState?.requirement || requirement.trim(),
      answers: [...(plannerState?.answers ?? []), ...answers],
      iteration: plannerState?.iteration ?? 0,
      lastQuestions: questions,
    };
    const answerSummary = answers
      .map((answer) => `${answer.question}: ${answer.label || answer.value}`)
      .join('\n');
    setPlannerState(nextState);
    sendPlannerMessage(`用户回答了本轮问题：\n${answerSummary}`, 'answer', nextState);
  };

  const handleFinalize = () => {
    if (!plannerState) return;
    sendPlannerMessage('请基于当前需求和已收集答案生成开发计划。', 'finalize', plannerState);
  };

  const applyPlanningPayload = (payload: RequirementPlannerPayload, answer: string) => {
    setAgentMessage(payload.message || answer);
    setPlannerState(payload.state);
    setQuestions(payload.questions ?? []);
    setDraftAnswers({});
    if (payload.plan) {
      setPlan(payload.plan);
      onPlanChange(payload.plan);
    }
  };

  const updateDraftAnswer = (questionId: string, value: DraftAnswerValue) => {
    setDraftAnswers((current) => ({ ...current, [questionId]: value }));
  };

  return (
    <section className={cx('requirement-planner-panel')}>
      <header className={cx('requirement-planner-header')}>
        <Text className={cx('editor-scope-tag')}>PLAN MODE</Text>
        <Title level={4}>需求规划</Title>
        <Text type="secondary">通过选择题逐步收敛需求，最终生成可保存的开发计划。</Text>
      </header>

      <div className={cx('requirement-planner-body')}>
        <Steps
          current={stepIndex}
          items={[
            { title: '输入需求', icon: <QuestionCircleOutlined /> },
            { title: '回答问题', icon: <OrderedListOutlined /> },
            { title: '生成计划', icon: <CheckCircleOutlined /> },
          ]}
          size="small"
        />

        {error && <Alert message={error} showIcon type="error" />}

        <section className={cx('planner-section')}>
          <Text strong>初始需求</Text>
          <TextArea
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={loading}
            placeholder={requirementPlaceholder}
            value={requirement}
            onChange={(event) => setRequirement(event.target.value)}
          />
          <Space wrap>
            <Button disabled={loading} onClick={handleStart} type="primary">
              开始规划
            </Button>
            <Button disabled={!canFinalize} icon={<SaveOutlined />} onClick={handleFinalize}>
              直接生成计划
            </Button>
            <Tag>已收集 {collectedAnswerCount} 条答案</Tag>
          </Space>
        </section>

        {loading && (
          <div className={cx('planner-loading')}>
            <Spin size="small" />
            <Text type="secondary">需求规划 agent 正在思考下一步...</Text>
          </div>
        )}

        {!loading && agentMessage && (
          <Alert
            className={cx('planner-agent-message')}
            message={<MarkdownContent content={agentMessage} />}
            showIcon
            type="info"
          />
        )}

        {!loading && questions.length > 0 && (
          <section className={cx('planner-section')}>
            <Title level={5}>请回答本轮问题</Title>
            <Space className={cx('planner-question-list')} direction="vertical" size={12}>
              {questions.map((question) => (
                <div className={cx('planner-question')} key={question.id}>
                  <Text strong>
                    {question.title}
                    {question.required && <Text type="danger"> *</Text>}
                  </Text>
                  {question.description && (
                    <Text className={cx('planner-question-description')} type="secondary">
                      {question.description}
                    </Text>
                  )}
                  {renderQuestionInput(question, draftAnswers[question.id], updateDraftAnswer)}
                </div>
              ))}
            </Space>
            <Space wrap>
              <Button onClick={handleSubmitAnswers} type="primary">
                提交答案并继续
              </Button>
              <Button disabled={!canFinalize} onClick={handleFinalize}>
                用当前答案生成计划
              </Button>
            </Space>
          </section>
        )}

        {!loading && plan ? (
          <PlanPreview plan={plan} />
        ) : (
          !loading &&
          questions.length === 0 && (
            <Empty description="输入需求后，规划 agent 会生成第一轮问题。" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )
        )}
      </div>
    </section>
  );
}

function renderQuestionInput(
  question: RequirementQuestion,
  value: DraftAnswerValue | undefined,
  onChange: (questionId: string, value: DraftAnswerValue) => void,
) {
  if (question.type === 'text') {
    return (
      <TextArea
        autoSize={{ minRows: 2, maxRows: 4 }}
        value={typeof value === 'string' ? value : ''}
        onChange={(event) => onChange(question.id, event.target.value)}
      />
    );
  }

  if (question.type === 'multiple') {
    return (
      <Checkbox.Group
        className={cx('planner-option-group')}
        value={Array.isArray(value) ? value : []}
        onChange={(checkedValues) => onChange(question.id, checkedValues.map(String))}
      >
        {question.options.map((option) => (
          <Checkbox className={cx('planner-option')} key={option.id} value={option.id}>
            <Text>{option.label}</Text>
            {option.description && <Text type="secondary">{option.description}</Text>}
          </Checkbox>
        ))}
      </Checkbox.Group>
    );
  }

  return (
    <Radio.Group
      className={cx('planner-option-group')}
      value={typeof value === 'string' ? value : undefined}
      onChange={(event) => onChange(question.id, event.target.value)}
    >
      {question.options.map((option) => (
        <Radio className={cx('planner-option')} key={option.id} value={option.id}>
          <Text>{option.label}</Text>
          {option.description && <Text type="secondary">{option.description}</Text>}
        </Radio>
      ))}
    </Radio.Group>
  );
}

function formatAnswerLabel(question: RequirementQuestion, value: DraftAnswerValue | undefined) {
  if (!value) return '';
  if (Array.isArray(value)) {
    return value
      .map((item) => question.options.find((option) => option.id === item)?.label ?? item)
      .join('、');
  }
  return question.options.find((option) => option.id === value)?.label ?? value;
}

function PlanPreview({ plan }: { plan: RequirementDevelopmentPlan }) {
  return (
    <section className={cx('planner-plan-preview')}>
      <Title level={5}>{plan.title}</Title>
      <Paragraph>{plan.summary}</Paragraph>

      {Boolean(plan.modules?.length) && (
        <Space wrap>
          {plan.modules?.map((module) => (
            <Tag color={module.enabled ? 'blue' : 'default'} key={module.name}>
              {module.name}
            </Tag>
          ))}
        </Space>
      )}

      <PlanList title="前端任务" values={plan.frontendTasks} />
      <PlanList title="后端任务" values={plan.backendTasks} />
      <PlanList title="下一步" values={plan.nextActions} />
    </section>
  );
}

function PlanList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;

  return (
    <div className={cx('planner-plan-list')}>
      <Text strong>{title}</Text>
      <ul>
        {values.map((value) => (
          <li key={value}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

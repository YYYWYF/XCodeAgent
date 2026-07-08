// import {
//   CheckCircleOutlined,
//   CloseCircleOutlined,
//   CodeOutlined,
//   FolderOpenOutlined,
//   PlayCircleOutlined,
//   SafetyCertificateOutlined,
// } from '@ant-design/icons';
// import { Alert, Button, Input, Modal, Space, Spin, Tag, Typography } from 'antd';
// import { useEffect, useMemo, useState } from 'react';
// import {
//   approveToolRequest,
//   isApprovalRequired,
//   rejectToolRequest,
//   runTerminalExec,
//   type TerminalExecRequest,
//   type TerminalExecResult,
//   type ToolApproval,
//   type ToolRiskLevel,
// } from '../../service/workspaceTools';
// import type { ApplicationConfig } from '../../typings';
// import { cx } from '../../utils';
// import './ProtectedToolPanel.less';
// 
// const { Text, Title } = Typography;
// 
// type PendingApproval = {
//   approval: ToolApproval;
//   request: TerminalExecRequest;
//   preview: TerminalExecResult;
// };
// 
// type Props = {
//   application: ApplicationConfig;
// };
// 
// const riskMeta: Record<ToolRiskLevel, { label: string; color: string }> = {
//   low: { label: '低风险', color: 'green' },
//   medium: { label: '需要确认', color: 'gold' },
//   high: { label: '高风险', color: 'red' },
// };
// 
// export default function ProtectedToolPanel({ application }: Props) {
//   const [workspaceRoot, setWorkspaceRoot] = useState(application.workspaceRoot ?? '');
//   const [cwd, setCwd] = useState('.');
//   const [command, setCommand] = useState('git status --short');
//   const [result, setResult] = useState<TerminalExecResult>();
//   const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null);
//   const [running, setRunning] = useState(false);
//   const [approving, setApproving] = useState(false);
//   const [error, setError] = useState<string>();
// 
//   const resultText = useMemo(() => (result ? formatTerminalResult(result) : ''), [result]);
//   const pendingRisk = pendingApproval?.approval.risk.level ?? 'low';
// 
//   useEffect(() => {
//     setWorkspaceRoot(application.workspaceRoot ?? '');
//   }, [application.workspaceRoot]);
// 
//   const buildRequest = (): TerminalExecRequest => ({
//     command: command.trim(),
//     cwd: cwd.trim() || '.',
//     timeout_seconds: 30,
//     max_output_chars: 20000,
//     workspace_root: workspaceRoot.trim() || undefined,
//   });
// 
//   const executeRequest = async (request: TerminalExecRequest) => {
//     const nextResult = await runTerminalExec(request);
//     if (isApprovalRequired(nextResult)) {
//       setPendingApproval({
//         approval: nextResult.approval,
//         request,
//         preview: nextResult,
//       });
//       return;
//     }
//     setResult(nextResult);
//   };
// 
//   const handleRun = async () => {
//     if (!command.trim() || running) return;
//     setRunning(true);
//     setError(undefined);
//     setResult(undefined);
//     try {
//       await executeRequest(buildRequest());
//     } catch (caughtError) {
//       setError(caughtError instanceof Error ? caughtError.message : '工具执行失败。');
//     } finally {
//       setRunning(false);
//     }
//   };
// 
//   const handleApprove = async () => {
//     if (!pendingApproval) return;
//     setApproving(true);
//     setError(undefined);
//     try {
//       const approval = await approveToolRequest(pendingApproval.approval.id);
//       await executeRequest({
//         ...pendingApproval.request,
//         approval: {
//           id: approval.id,
//           token: approval.token,
//         },
//       });
//       setPendingApproval(null);
//     } catch (caughtError) {
//       setError(caughtError instanceof Error ? caughtError.message : '审批执行失败。');
//     } finally {
//       setApproving(false);
//     }
//   };
// 
//   const handleReject = async () => {
//     if (!pendingApproval || approving) return;
//     setApproving(true);
//     setError(undefined);
//     try {
//       await rejectToolRequest(pendingApproval.approval.id);
//       setResult({
//         tool: 'terminal.exec',
//         argv: pendingApproval.preview.argv,
//         cwd: pendingApproval.preview.cwd,
//         risk: pendingApproval.approval.risk,
//         executed: false,
//         requires_approval: false,
//         returncode: null,
//         stdout: '',
//         stderr: '用户已拒绝本次工具执行。',
//       });
//       setPendingApproval(null);
//     } catch (caughtError) {
//       setError(caughtError instanceof Error ? caughtError.message : '拒绝审批失败。');
//     } finally {
//       setApproving(false);
//     }
//   };
// 
//   return (
//     <section className={cx('protected-tool-panel')}>
//       <header className={cx('protected-tool-header')}>
//         <Text className={cx('protected-tool-eyebrow')}>PROTECTED TOOL</Text>
//         <Title level={4}>受保护命令</Title>
//       </header>
// 
//       <div className={cx('protected-tool-form')}>
//         <label>
//           <Text>工作区</Text>
//           <Input
//             allowClear
//             prefix={<FolderOpenOutlined />}
//             placeholder="默认使用后端工作区"
//             value={workspaceRoot}
//             onChange={(event) => setWorkspaceRoot(event.target.value)}
//           />
//         </label>
//         <label>
//           <Text>目录</Text>
//           <Input value={cwd} onChange={(event) => setCwd(event.target.value)} />
//         </label>
//         <label>
//           <Text>命令</Text>
//           <Input
//             prefix={<CodeOutlined />}
//             value={command}
//             onChange={(event) => setCommand(event.target.value)}
//             onPressEnter={handleRun}
//           />
//         </label>
//         <Button
//           block
//           disabled={!command.trim() || running}
//           icon={<PlayCircleOutlined />}
//           loading={running}
//           type="primary"
//           onClick={handleRun}
//         >
//           执行
//         </Button>
//       </div>
// 
//       <div className={cx('protected-tool-result')} aria-live="polite">
//         {error && <Alert message={error} showIcon type="error" />}
//         {!error && running && (
//           <div className={cx('protected-tool-running')}>
//             <Spin size="small" />
//             <Text type="secondary">等待工具返回...</Text>
//           </div>
//         )}
//         {!error && result && (
//           <>
//             <div className={cx('protected-tool-status')}>
//               {result.executed ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
//               <Text strong>{result.executed ? '已执行' : '未执行'}</Text>
//               {result.risk && (
//                 <Tag color={riskMeta[result.risk.level].color}>
//                   {riskMeta[result.risk.level].label}
//                 </Tag>
//               )}
//             </div>
//             <pre>{resultText}</pre>
//           </>
//         )}
//       </div>
// 
//       <Modal
//         cancelButtonProps={{ disabled: approving }}
//         cancelText="拒绝"
//         confirmLoading={approving}
//         okText="允许执行"
//         open={Boolean(pendingApproval)}
//         title={
//           <Space>
//             <SafetyCertificateOutlined />
//             <span>{pendingApproval?.approval.title}</span>
//           </Space>
//         }
//         onCancel={handleReject}
//         onOk={handleApprove}
//       >
//         {pendingApproval && (
//           <div className={cx('approval-modal-body')}>
//             <div className={cx('approval-subject')}>
//               <Tag color={riskMeta[pendingRisk].color}>{riskMeta[pendingRisk].label}</Tag>
//               <Text code>{pendingApproval.approval.subject}</Text>
//             </div>
//             <Text>{pendingApproval.approval.description}</Text>
//             {pendingApproval.approval.risk.reasons.length > 0 && (
//               <ul>
//                 {pendingApproval.approval.risk.reasons.map((reason) => (
//                   <li key={reason}>{reason}</li>
//                 ))}
//               </ul>
//             )}
//             {pendingApproval.approval.details && (
//               <pre>{pendingApproval.approval.details}</pre>
//             )}
//           </div>
//         )}
//       </Modal>
//     </section>
//   );
// }
// 
// function formatTerminalResult(result: TerminalExecResult) {
//   const lines = [
//     `$ ${(result.argv ?? []).join(' ')}`,
//     `cwd: ${result.cwd ?? '.'}`,
//     `returncode: ${result.returncode ?? 'none'}`,
//   ];
// 
//   if (result.timed_out) {
//     lines.push('timed_out: true');
//   }
//   if (result.stdout) {
//     lines.push('', 'stdout:', result.stdout.trimEnd());
//   }
//   if (result.stderr) {
//     lines.push('', 'stderr:', result.stderr.trimEnd());
//   }
// 
//   return lines.join('\n');
// }

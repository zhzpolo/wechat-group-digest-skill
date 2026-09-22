import argparse
import json
from pathlib import Path
import sys
from .messages import time_window


def main():
    parser = argparse.ArgumentParser(description='本人本地微信指定群导出；总结由当前 Codex 会话撰写，render 校验后生成离线报告。')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='只检查环境，不读取聊天正文或输出密钥')
    demo = commands.add_parser('demo', help='生成明确标注的虚构数据测试报告')
    demo.add_argument('--output', default='outputs')
    exp = commands.add_parser('export', help='读取指定群；仅导出消息，不自动生成总结')
    exp.add_argument('--group', help='完整群名，不接受模糊匹配')
    exp.add_argument('--group-id')
    exp.add_argument('--account', help='多个账号时明确选择 db_storage 路径')
    exp.add_argument('--pid', type=int)
    exp.add_argument('--hours', type=int, choices=[24,48,72], default=24)
    exp.add_argument('--start')
    exp.add_argument('--end')
    exp.add_argument('--output', default='outputs')
    exp.add_argument('--capture', metavar='ANCHORS_JSON', help='在线读取失败后用于启动捕获的、经静态检查的本机 anchor')
    exp.add_argument('--capture-seconds', type=int, default=120)
    exp.add_argument('--online-attempts', type=int, default=30, help='在线稳定快照的最大连续检查次数，默认 30')
    exp.add_argument('--restart-fallback', '--wait-for-close', dest='restart_fallback', action='store_true',
                     help='仅在线稳定快照或密钥验证失败后，提示从托盘退出并进行启动捕获')
    report = commands.add_parser('render', help='校验 Codex 撰写的 report.json 并渲染 HTML/Markdown/PNG')
    report.add_argument('directory', type=Path)
    batch = commands.add_parser('batch', help='完整输出一个批次，供 Codex 逐批阅读')
    batch.add_argument('directory', type=Path)
    batch.add_argument('number', type=int)
    args = parser.parse_args()
    if args.command == 'doctor':
        import platform
        from importlib.metadata import version
        from .windows import accounts, processes
        print(json.dumps({'os':platform.platform(),'python':sys.version,'accounts':[str(p) for p in accounts()],
                          'processes':processes(),'packages':{n:version(n) for n in ['apsw','pycryptodome','zstandard','psutil','playwright','pillow']},
                          'edge_found':Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe').exists(),
                          'real_read_verified':None,'note':'doctor 不执行真实读取；已验证范围与限制见 references/OPERATIONS.md。'},ensure_ascii=False,indent=2))
    elif args.command == 'demo':
        from .demo import demo
        print(demo(args.output).resolve())
    elif args.command == 'export':
        from .reader import export
        name = args.group
        if not name:
            if not sys.stdin.isatty():
                raise ValueError('缺少群名。请提供 --group "完整群名"；不会擅自选择其他群。')
            name = input('请输入微信群完整名称：').strip()
        if not name: raise ValueError('群名不能为空')
        window = time_window(args.hours,args.start,args.end)
        print('固定时间范围：',window['start'],'≤ 时间 <',window['end'],window['timezone'],flush=True)
        if args.restart_fallback and not args.capture:
            raise ValueError('--restart-fallback 需要 --capture')
        result = export(name,window,args.output,args.account,args.pid,args.group_id,args.capture,
                        args.capture_seconds,args.restart_fallback,args.online_attempts)
        print('已导出全部选定消息：',result.resolve())
        print('下一步：让当前 Codex 会话阅读全部 batches 并撰写 report.json，然后运行 render。')
    elif args.command == 'render':
        from .report import render,screenshot
        render(args.directory)
        info = screenshot(args.directory)
        print(json.dumps(info,ensure_ascii=False,indent=2))
    elif args.command == 'batch':
        file = args.directory/'batches'/f'batch-{args.number:04d}.json'
        print(file.read_text(encoding='utf-8'))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, PermissionError, FileNotFoundError) as exc:
        print('未完成：' + str(exc),file=sys.stderr)
        sys.exit(2)

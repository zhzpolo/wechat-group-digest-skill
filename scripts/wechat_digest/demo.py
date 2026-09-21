"""Clearly labelled fictional sample; never substitutes for a real export."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid
from .messages import TZ, normalize, stats, time_window, write_messages
from .report import render, screenshot


def demo(root):
    group = '虚构项目协作群 <离线安全测试>'
    gid = 'fictional-demo@chatroom'
    window = time_window(start='2026-09-19T09:00:00+08:00', end='2026-09-20T09:00:00+08:00')
    samples = [
        (1, '甲', 1, '建议下周一上午开评审会，大家觉得如何？'),
        (2, '乙', 1, '收到，我还没确认时间。'),
        (3, '丙', 1, '我同意先整理问题清单，会议时间还需确认。'),
        (4, '甲', 1, '决定先整理问题清单。乙负责，周日18:00前发到群里。'),
        (5, '乙', 1, '问题清单已整理并发到群里。'),
        (6, '甲', 1, '已收到问题清单，内容核对无误。'),
        (7, '丙', 49, '<msg><appmsg><title>接口规范初稿</title><type>6</type><appattach><fileext>pdf</fileext><totallen>1024</totallen></appattach></appmsg></msg>'),
        (8, '甲', 49, '<msg><appmsg><title>接口是否兼容旧版，还需要验证。</title><type>57</type><refermsg><svrid>7</svrid><displayname>丙</displayname><content>接口规范初稿</content><type>49</type></refermsg></appmsg></msg>'),
        (9, '乙', 3, '<msg><img /></msg>'),
        (10, '丙', 34, '<msg><voicemsg /></msg>'),
        (11, '甲', 1, '请补充旧版接口兼容性验证结果，负责人和截止时间下次再定。'),
        (12, '乙', 1, '<script>alert("虚构注入测试")</script> 这是一段测试文本，不应执行。'),
    ]
    messages = []
    for i, sender, typ, content in samples:
        row = {'local_id':i,'server_id':i,'create_time':int(window['start_epoch'])+i*600,
               'real_sender_id':ord(sender),'local_type':typ,'message_content':content}
        m = normalize(row,'fictional.db',gid,1,{ord(sender):sender})
        messages.append(m)
    directory = Path(root) / ('demo-' + datetime.now(TZ).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    metadata = {'group_name':group,'group_id':gid,'account_id':'fictional','window':window,'synthetic':True,
                'created_at':datetime.now(TZ).isoformat(),'warnings':['全部消息均为虚构，专用于解析、引用、排版和渲染验证。'],
                'deduplicated_count':0,'raw_selected_count':len(messages),'shards':[], 'plaintext_database_files_created':0}
    write_messages(directory, metadata, messages)
    def claim(text, category, nums):
        return {'text':text,'category':category,'evidence':[{'message_id':messages[n-1]['id'],'quote':messages[n-1]['text']} for n in nums]}
    report = {'schema_version':1,'synthetic':True,'author':'固定虚构测试样例，非自动模型总结',
              'messages_sha256':hashlib.sha256((directory/'messages.json').read_bytes()).hexdigest(),
              'reviewed_batches':[b['sha256'] for b in json.loads((directory/'batches/manifest.json').read_text(encoding='utf-8'))],
              'overview':claim('群内讨论评审准备与接口兼容性。问题清单已由乙提交并获甲确认；会议时间与旧版兼容性仍待确认。','其他',[1,2,5,6,8]),
              'topics':[claim('甲建议下周一上午开评审会；乙仅表示收到且尚未确认，不能视为会议已确定。','建议',[1,2]),
                        claim('甲决定由乙整理问题清单，要求周日18:00前提交。','决定',[4]),
                        claim('丙发送接口规范初稿文件卡片；甲指出旧版兼容性尚需验证。未读取附件内容。','群内观点',[7,8])],
              'todos':[{**claim('补充旧版接口兼容性验证结果。','未解决',[11]),'owner':'未明确','deadline':'未明确','status':'待验证；未见完成记录'}],
              'resolved':[claim('乙自述已提交问题清单，甲随后确认收到且核对无误。','已确认',[5,6])],
              'open_questions':[claim('评审会议时间是否最终确定？本次记录中未见决定。','未解决',[1,2]),claim('接口能否兼容旧版？本次记录中未见验证结果。','未解决',[8,11])],
              'other':[claim('记录包含一条图片和一条语音，未解析其内容。','其他',[9,10])]}
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    render(directory)
    screenshot(directory)
    return directory

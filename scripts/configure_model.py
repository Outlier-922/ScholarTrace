"""Local terminal wizard. Hidden key input; never send credentials to the UI."""
from __future__ import annotations
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings
from dotenv import set_key,load_dotenv

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from backend.app import model_client,model_status


def save_configuration(path,values):
    """Atomic replacement preserves unrelated settings and comments."""
    path=Path(path)
    if path.is_symlink():raise ValueError('symlink_env_not_supported')
    allowed={'LLM_BASE_URL','LLM_API_KEY','LLM_MODEL','LLM_API_PROFILE','LLM_REASONING_EFFORT'}
    if set(values)-allowed:raise ValueError('invalid_setting')
    key=values.get('LLM_API_KEY','')
    if not re.fullmatch(r'[\x21-\x7e]{8,500}',key):raise ValueError('invalid_key_input')
    if any('\n' in value or '\r' in value or '\x00' in value for value in values.values()):raise ValueError('invalid_setting')
    original=path.read_text(encoding='utf-8-sig') if path.exists() else ''
    name=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.env.',suffix='.tmp',delete=False) as stream:
            stream.write(original);name=Path(stream.name)
        for setting,value in values.items():set_key(str(name),setting,value,quote_mode='always',encoding='utf-8')
        os.replace(name,path)
    finally:
        if name and name.exists():name.unlink()


def main():
    parser=argparse.ArgumentParser(description='只在本机终端输入API Key，不经聊天或浏览器。')
    parser.add_argument('--provider',choices=['deepseek','openai'],default='deepseek')
    args=parser.parse_args()
    if not sys.stdin.isatty():
        print('请双击项目中的“配置模型.cmd”，在本机终端中输入；不支持会回显密钥的管道输入。')
        return 2
    print('\n研迹 ScholarTrace · 本机模型配置\n')
    print('DeepSeek API Key 页面：https://platform.deepseek.com/api_keys')
    print('密钥只写入本机项目的 .env（明文配置，已排除Git），不会显示或发送到聊天。')
    print('保存后会发送一次不含论文的固定JSON连接检查，请确保账号可用。\n')
    if args.provider=='deepseek':
        endpoint='https://api.deepseek.com';model='deepseek-flash';profile='compatible'
        print('服务：DeepSeek；型号：deepseek-flash；其他参数自动填写。')
    else:
        endpoint='https://api.openai.com/v1';profile='openai'
        model=input('输入你账号可用且支持JSON输出的API模型名：').strip()
        if not model or len(model)>150:print('模型名为空或过长，未保存。');return 2
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('粘贴 API Key 后按 Enter（输入不会显示；取消按 Ctrl+C）：').strip()
    if not key:print('没有输入密钥，原配置保持不变。');return 2
    path=ROOT/'.env'
    save_configuration(path,{'LLM_BASE_URL':endpoint,'LLM_API_KEY':key,'LLM_MODEL':model,'LLM_API_PROFILE':profile,'LLM_REASONING_EFFORT':''})
    key=None
    load_dotenv(path,override=True)
    print('\n已保存。正在测试连接；不会上传论文。')
    result=asyncio.run(model_status.probe())
    if result['status']=='connected':
        print('连接成功。关闭之前的本地服务后，双击“启动科研助手.cmd”。')
        print('如果助手正在等待配置，只需回复“已配置”，不要发送密钥。')
        return 0
    messages={'authentication_failed':'密钥无效或没有访问权限，请重新复制API Key。',
              'rate_limited':'请求被限流，请稍后重试；不要连续点击。',
              'timeout':'网络请求超时，请稍后再试。',
              'network_error':'网络连接失败，请检查本机网络。',
              'provider_error':'服务商拒绝请求，请在官方平台检查额度与模型访问权限。',
              'invalid_output_or_configuration':'未得到有效JSON响应，请核对模型是否支持此接口。'}
    print('配置已保存，但连接检查未通过。'+messages.get(result.get('error_code'),'请在官方平台检查配置。'))
    print('原始服务商响应和密钥均未输出。可重新双击“配置模型.cmd”。')
    return 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (KeyboardInterrupt,EOFError):print('\n已取消。');raise SystemExit(2)
    except Exception as error:
        print(f'配置未完成（{type(error).__name__}），未输出密钥。请检查终端和文件是否可用。')
        raise SystemExit(1)

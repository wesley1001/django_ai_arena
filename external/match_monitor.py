from multiprocessing import Process
import os, sys, socket, json
from sqlite3 import connect
from .helpers_core import queue_io


# 多进程支持
def setup_django():
    '''在多进程内挂载django数据库'''
    import django
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.append(BASE_DIR)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'main.settings'
    django.setup()


def unit_monitor(type, name, data, error_logger=None):
    '''
    比赛维护进程
    每个监控进程维护一个比赛进程
    监控数据库获取其维护比赛的状态，并进行中止等操作
    '''
    # 初始化
    setup_django()
    from django.conf import settings
    from time import perf_counter as pf, sleep
    from match_sys import models
    from . import helpers
    from .factory import Factory

    # 重定向错误输出
    if error_logger:
        import sys
        sys.stdout = sys.stderr = queue_io(error_logger)

    # 运行比赛进程
    match_dir = os.path.join(settings.PAIRMATCH_DIR, name)
    os.makedirs(match_dir, exist_ok=1)
    if type == None:  # 扩展区域
        pass
    else:  # 默认type=='match'一对一比赛
        AI_type, params = data
        match_process = Factory(AI_type, name, params, error_logger)
    match_process.start()

    # 循环监测运行状态与数据库
    cycle = 0
    while 1:
        # 检查运行状况
        now = pf()
        if not match_process.check_active(now):
            break

        # 检查数据库注册条目
        cycle += 1
        if cycle >= settings.MONITOR_DB_CHECK_CYCLE:
            cycle -= settings.MONITOR_DB_CHECK_CYCLE
            match_process.reload_match()
            if match_process.match.status == -1:  # 外部中止
                match_process.timeout = -1

        sleep(settings.MONITOR_CYCLE)  # 待机


def start_match(AI_type,
                code1,
                code2,
                param_form,
                ranked=False,
                join=False,
                error_logger=None):
    from match_sys import models
    from . import helpers
    from django.utils import timezone
    from django.db import connections

    # 生成随机match名称
    while 1:
        match_name = helpers.gen_random_string()
        if not models.PairMatch.objects.filter(name=match_name):
            break

    # 获取match参数
    if isinstance(param_form, dict):
        params = param_form
    else:
        params = param_form.cleaned_data

    # 创建未启动比赛对象
    new_match = models.PairMatch()
    new_match.ai_type = AI_type
    new_match.name = match_name
    new_match.code1 = models.Code.objects.get(id=code1)
    new_match.code2 = models.Code.objects.get(id=code2)
    new_match.old_score1 = new_match.code1.score
    new_match.old_score2 = new_match.code2.score
    new_match.rounds = params['rounds']
    new_match.is_ranked = ranked
    new_match.params = json.dumps(params)
    new_match.save()

    # 阻塞参数直接发起比赛
    # 传送参数至进程 (AI_type,code1,code2,match_name,params)
    if join:
        match_proc = Process(
            target=unit_monitor,
            args=('match', match_name, [AI_type, params], error_logger))
        connections.close_all()  # 用于主进程MySQL保存所有更改
        match_proc.start()
        return match_proc

    # 否则仅创建
    # 返回match对象名称
    return match_name


def kill_match(type, match_name):
    from match_sys import models
    match = models.PairMatch.objects.get(name=match_name)
    match.status = -1
    match.save()

import argparse
import shlex
import asyncio
import json

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

from .fetcher import fetch_player_data, fetch_player_stats, fetch_pandascore_matches, fetch_player_seasonal_stats, fetch_r6_wiki, fetch_game_online_stats
from .formatter import format_new_overview, format_match_history, calculate_kd, calculate_winrate
from .formatter3 import format_pandascore_matches
from . import formatter_wiki as wiki_fmt


class R6StatesPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    def create_parser(self):
        parser = argparse.ArgumentParser(prog="R6", add_help=False)
        parser.add_argument("-g", "--group", action="store_true")
        parser.add_argument("ids", nargs="*")
        parser.add_argument("-h", "--help", action="store_true")
        return parser

    async def query_player_overview(self, player_id: str) -> str:
        try:
            api_key = self.config.get("api_key", "")
            
            # Fetch parallel data pure API
            stats_task = fetch_player_stats(player_id, api_key)
            ops_task = fetch_player_data(player_id, api_key)
            
            stats_data, ops_data = await asyncio.gather(stats_task, ops_task)
            
            if "error" in stats_data and stats_data["error"]:
                return f"获取 {player_id} 错误: {stats_data['error']}"
            return format_new_overview(player_id, stats_data, ops_data)
        except Exception as e:
            logger.error(f"查询玩家 {player_id} 失败: {type(e).__name__}: {e}")
            return f"❌ 查询玩家 {player_id} 失败，可能是由于ID错误、网络延迟或 API 维护中。"

    async def query_player_overview_raw(self, player_id: str) -> dict:
        try:
            api_key = self.config.get("api_key", "")

            stats_task = fetch_player_stats(player_id, api_key)
            ops_task = fetch_player_data(player_id, api_key)

            stats_data, ops_data = await asyncio.gather(stats_task, ops_task)

            if isinstance(stats_data, dict) and stats_data.get("error"):
                return {
                    "ok": False,
                    "player_id": player_id,
                    "error": stats_data.get("error")
                }
            if isinstance(ops_data, dict) and ops_data.get("error"):
                return {
                    "ok": False,
                    "player_id": player_id,
                    "error": ops_data.get("error")
                }

            season = []
            rank = {
                "season_id": "未知",
                "max_rank": 0,
                "max_rp": 0,
                "current_rank": 0,
                "current_rp": 0
            }

            profiles = stats_data.get("platform_families_full_profiles", []) if isinstance(stats_data, dict) else []
            if isinstance(profiles, list) and profiles:
                boards = profiles[0].get("board_ids_full_profiles", [])
                if isinstance(boards, list):
                    for board in boards:
                        if not isinstance(board, dict):
                            continue

                        board_id = board.get("board_id", "")
                        mode_names = {
                            "ranked": "排位",
                            "standard": "快速",
                            "living_game_mode": "非排"
                        }
                        mode_name = mode_names.get(board_id)
                        if not mode_name:
                            continue

                        inner_profiles = board.get("full_profiles", [])
                        if not isinstance(inner_profiles, list) or not inner_profiles:
                            continue

                        profile_item = inner_profiles[0] if isinstance(inner_profiles[0], dict) else {}
                        if board_id == "ranked":
                            profile = profile_item.get("profile", {})
                            if isinstance(profile, dict):
                                rank = {
                                    "season_id": profile.get("season_id", "未知"),
                                    "max_rank": profile.get("max_rank", 0),
                                    "max_rp": profile.get("max_rank_points", 0),
                                    "current_rank": profile.get("rank", 0),
                                    "current_rp": profile.get("rank_points", 0)
                                }

                        stats = profile_item.get("season_statistics", {})
                        if not isinstance(stats, dict):
                            stats = {}
                        kills = stats.get("kills", 0)
                        deaths = stats.get("deaths", 0)
                        outcomes = stats.get("match_outcomes", {})
                        if not isinstance(outcomes, dict):
                            outcomes = {}
                        wins = outcomes.get("wins", 0)
                        losses = outcomes.get("losses", 0)
                        abandons = outcomes.get("abandons", 0)

                        season.append({
                            "mode": mode_name,
                            "kd": calculate_kd(kills, deaths),
                            "winrate": calculate_winrate(wins, losses),
                            "kills": kills,
                            "deaths": deaths,
                            "matches": wins + losses + abandons
                        })

            operator_plays = {}
            split_data = ops_data.get("split", {}) if isinstance(ops_data, dict) else {}
            if isinstance(split_data, dict):
                pc_data = split_data.get("pc", {})
                playlists = pc_data.get("playlists", {}) if isinstance(pc_data, dict) else {}
                if isinstance(playlists, dict):
                    for playlist_data in playlists.values():
                        if not isinstance(playlist_data, dict):
                            continue
                        operators = playlist_data.get("operators", {})
                        if not isinstance(operators, dict):
                            continue
                        for op_info in operators.values():
                            if not isinstance(op_info, dict):
                                continue
                            name = op_info.get("operator", "Unknown")
                            rounds = op_info.get("rounds", {})
                            if not isinstance(rounds, dict):
                                rounds = {}
                            seasonal = rounds.get("seasonal", {})
                            lifetime = rounds.get("lifetime", {})
                            if not isinstance(seasonal, dict):
                                seasonal = {}
                            if not isinstance(lifetime, dict):
                                lifetime = {}
                            played = seasonal.get("played", 0)
                            won = seasonal.get("won", 0)
                            if played == 0:
                                played = lifetime.get("played", 0)
                                won = lifetime.get("won", 0)
                            if name not in operator_plays:
                                operator_plays[name] = {"played": 0, "won": 0}
                            operator_plays[name]["played"] += played
                            operator_plays[name]["won"] += won

            top_operators = []
            sorted_ops = sorted(operator_plays.items(), key=lambda x: x[1]["played"], reverse=True)
            for name, data in [op for op in sorted_ops if op[1]["played"] > 0][:3]:
                played = data.get("played", 0)
                won = data.get("won", 0)
                top_operators.append({
                    "name": name,
                    "played": played,
                    "won": won,
                    "winrate": calculate_winrate(won, played - won)
                })

            return {
                "ok": True,
                "player_id": player_id,
                "season": season,
                "rank": rank,
                "top_operators": top_operators,
                "reply_instruction": "请基于 data 用自然语言总结，不要逐字段复述 JSON，不要编造数据。"
            }
        except Exception as e:
            logger.error(f"查询玩家 {player_id} 结构化数据失败: {type(e).__name__}: {e}")
            return {
                "ok": False,
                "player_id": player_id,
                "error": "查询玩家结构化数据失败，可能是由于ID错误、网络延迟或 API 维护中。"
            }

    async def generate_llm_tool_reply(self, event: AstrMessageEvent, title: str, data) -> str:
        try:
            provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            persona = self.context.persona_manager.get_default_persona_v3(umo=event.unified_msg_origin)
            if asyncio.iscoroutine(persona):
                persona = await persona
            persona_prompt = ""
            if isinstance(persona, dict):
                persona_prompt = persona.get("prompt", "")
            else:
                persona_prompt = getattr(persona, "prompt", "")

            prompt = (
                f"你正在扮演当前 AstrBot 人格，请严格遵守下面的人格设定来回复用户。\n"
                f"【当前人格设定】\n{persona_prompt}\n\n"
                f"【任务】\n"
                f"请基于插件查询到的 {title} 数据，用自然语言给用户一个简洁、有帮助的回复。\n"
                f"不要逐字段复述 JSON，不要编造数据；如果数据中包含 error 或 ok=false，请直接说明查询失败原因。\n\n"
                f"【查询结果 JSON】\n"
                f"{json.dumps(data, ensure_ascii=False)}"
            )
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt
            )
            return llm_resp.completion_text
        except Exception as e:
            logger.error(f"生成 R6 LLM Tool 回复失败: {type(e).__name__}: {e}")
            return json.dumps(data, ensure_ascii=False)

    @filter.command("r6")
    async def r6_command(self, event: AstrMessageEvent, message: str = ""):
        '''查询 R6S 战绩: /R6 [-g] [-h] <id1> <id2>'''
        parser = self.create_parser()
        
        if message:
            args_list = shlex.split(message)
        else:
            parts = shlex.split(event.message_str)
            args_list = parts[1:] if len(parts) > 1 else []
            
        if not args_list:
            yield event.plain_result(
                "/R6 [options] <id1> <id2> ...\n"
                "-g, --group    开启组队模式喵 (最多5个喵)\n"
                "-h, --help     查看完整帮助喵\n"
                "/R6M <名称>          查询赛区排期与比分\n"
                "/R6H <ID>            查询最近战绩变动记录\n"
                "/R6OP <名称>         查询干员详细百科信息\n"
                "/R6S                 查看服务器当前在线人数\n"
            )
            return

        try:
            parsed_args = parser.parse_args(args_list)
        except SystemExit:
            yield event.plain_result("参数解析错误喵。用法: /R6 [-g] [-h] [ids...]喵")
            return

        if parsed_args.help:
            yield event.plain_result(
                "/R6 [options] <id1> <id2> ...\n"
                "-g, --group    开启组队模式 (最多5个)喵\n"
                "-m, --map      指定地图喵\n"
                "-h, --help     查看完整帮助喵\n"
                "/R6M <队伍名/赛区>   查询赛区排期与比分喵\n"
                "/R6H <ID>            查询最近战绩变动记录喵\n"
                "/R6OP <干员名>       查询干员详细百科信息喵\n"
                "/R6S                 查看服务器当前在线人数喵\n"
            )
            return

        ids = parsed_args.ids
        is_group = parsed_args.group

        if not ids:
            yield event.plain_result("未提供ID喵，使用/R6 -h 查看用法喵")
            return

        if is_group:
            if len(ids) > 5:
                yield event.plain_result("组队模式最多只能查询5个ID喵")
                return
            tasks = [self.query_player_overview(idx) for idx in ids]
            results = await asyncio.gather(*tasks)
            for res in results:
                yield event.plain_result(res)
            yield event.plain_result("查询完毕喵！")
        else:
            if len(ids) != 1:
                yield event.plain_result("单人模式只能查询1个ID喵，多ID请使用 -g 参数喵：/R6 -g id1 id2")
                return
            res = await self.query_player_overview(ids[0])
            yield event.plain_result(res)

    @filter.command("r6dapi")
    async def set_api_key(self, event: AstrMessageEvent, message: str = ""):
        '''配置 R6Data API Key (管理员或直接在WebUI配置喵)'''
        key = message.strip()
        if not key:
            parts = event.message_str.split(maxsplit=1)
            if len(parts) > 1:
                key = parts[1].strip()
            
        if not key:
            yield event.plain_result("请在命令中输入 API Key喵")
            return
        
        self.config["api_key"] = key
        self.config.save_config()
        yield event.plain_result("✅ API Key 已更新，建议在 WebUI 固化配置以免重启失效喵")

    @filter.command("r6m")
    async def r6m_command(self, event: AstrMessageEvent, message: str = ""):
        '''查询 R6S 赛事: /R6M <队伍名或赛区简称>'''
        query = message.strip()
        if not query:
            parts = event.message_str.split(maxsplit=1)
            if len(parts) > 1:
                query = parts[1].strip()
                
        if not query:
            yield event.plain_result("请在命令后输入队伍名 (如 G2) 或赛区简称 (如 EU, CNL)。用法: /R6M <队伍名/赛区>")
            return
            
        api_key = self.config.get("pandascore_api_key", "")
        if not api_key:
            yield event.plain_result("❌ 未配置 pandascore_api_key，请在 WebUI 或 _conf_schema.json 中配置。")
            return
            
        data = await fetch_pandascore_matches(query, api_key)
        res = format_pandascore_matches(data)
        yield event.plain_result(res)

    @filter.command("r6h")
    async def r6h_command(self, event: AstrMessageEvent, player_id: str):
        '''查询 R6S 战绩变动: /R6H <id>'''
        api_key = self.config.get("api_key", "")
        data = await fetch_player_seasonal_stats(player_id, api_key)
        yield event.plain_result(format_match_history(player_id, data))

    @filter.command("r6op")
    async def r6op_command(self, event: AstrMessageEvent, name: str):
        '''查询 R6S 干员百科: /R6OP <干员名>'''
        api_key = self.config.get("api_key", "")
        data = await fetch_r6_wiki("operators", api_key, params={"name": name})
        yield event.plain_result(wiki_fmt.format_operator_info(data, name))

    @filter.command("r6map")
    async def r6map_command(self, event: AstrMessageEvent, name: str):
        '''查询 R6S 地图百科: /R6MAP <地图名>'''
        api_key = self.config.get("api_key", "")
        # 尝试将中文翻译回英文
        eng_name = wiki_fmt.translate_map_query(name)
        data = await fetch_r6_wiki("maps", api_key, params={"name": eng_name})
        yield event.plain_result(wiki_fmt.format_map_info(data, eng_name))

    @filter.command("r6w")
    async def r6w_command(self, event: AstrMessageEvent, name: str):
        '''查询 R6S 武器百科: /R6W <武器名>'''
        api_key = self.config.get("api_key", "")
        data = await fetch_r6_wiki("weapons", api_key, params={"name": name})
        yield event.plain_result(wiki_fmt.format_weapon_info(data, name))


    @filter.command("r6s")
    async def r6s_online_command(self, event: AstrMessageEvent):
        '''查询 R6S 服务器在线人数: /R6S'''
        api_key = self.config.get("api_key", "")
        data = await fetch_game_online_stats(api_key)
        yield event.plain_result(wiki_fmt.format_game_stats(data))

    @filter.llm_tool(name="query_r6_player_stats")

    async def query_r6_player_stats(self, event: AstrMessageEvent, player_id: str):
        '''查询获取彩虹六号(Rainbow Six Siege, R6S)玩家的最新战绩、当前排位分数以及最常用干员数据喵。

        Args:
            player_id(string): 玩家的育碧(Ubisoft)游戏ID
        '''
        data = await self.query_player_overview_raw(player_id)
        res = await self.generate_llm_tool_reply(event, f"玩家 {player_id} 战绩", data)
        yield event.plain_result(res)

    @filter.llm_tool(name="query_r6_esports_matches")
    async def query_r6_esports_matches(self, event: AstrMessageEvent, query: str):
        '''查询彩虹六号(Rainbow Six Siege, R6S)的电竞战队或赛区/联赛的近期比赛排期和比分结果。

        Args:
            query(string): 队伍名(如 G2, W7M) 或者是赛区简称(如 eu, cn, apl, na, Europe)
        '''
        api_key = self.config.get("pandascore_api_key", "")
        if not api_key:
            yield event.plain_result("❌ 未配置 pandascore_api_key。")
            return
        data = await fetch_pandascore_matches(query, api_key)
        res = await self.generate_llm_tool_reply(event, f"彩虹六号电竞比赛（{query}）", data)
        yield event.plain_result(res)

    @filter.llm_tool(name="query_r6_operator_info")
    async def query_r6_operator_info(self, event: AstrMessageEvent, operator_name: str):
        '''获取彩虹六号(R6S)指定干员的详细百科信息，包括属性、背景组织、侧翼等。

        Args:
            operator_name(string): 干员名称(如 Ash, Jäger)
        '''
        api_key = self.config.get("api_key", "")
        data = await fetch_r6_wiki("operators", api_key, params={"name": operator_name})
        res = await self.generate_llm_tool_reply(event, f"干员 {operator_name} 百科", data)
        yield event.plain_result(res)


    @filter.llm_tool(name="query_r6_game_online_status")
    async def query_r6_game_online_status(self, event: AstrMessageEvent):
        '''查看彩虹六号当前全球各平台的实时在线人数和注册用户统计。'''
        api_key = self.config.get("api_key", "")
        data = await fetch_game_online_stats(api_key)
        res = await self.generate_llm_tool_reply(event, "游戏在线状态", data)
        yield event.plain_result(res)

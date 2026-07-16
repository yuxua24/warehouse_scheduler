#!/usr/bin/env python3
"""评测入口：运行规划器评测 + Agent 评测 + 多模型对比。

使用方法:
    python eval/run_all.py                                    # 运行规划器 + Agent 评测
    python eval/run_all.py --mode planner                     # 仅运行规划器评测
    python eval/run_all.py --mode agent                       # 仅运行 Agent 评测
    python eval/run_all.py --mode agent --model deepseek-chat # 指定模型
    python eval/run_all.py --mode compare --models deepseek-chat,gpt-4o-mini  # 多模型对比
    python eval/run_all.py --mode all                         # 运行全部
"""

import argparse
import json
import os
import sys
import time

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from eval.runner import run_scenario_eval, print_case_details
from eval.reporter import generate_json_report, generate_markdown_report
from eval.comparators import compare_baselines
from eval.agent_runner import run_agent_eval, make_tool_manager
from eval.agent_reporter import generate_agent_report, generate_model_comparison
from eval.model_config import load_model_configs, ModelConfig


def main():
    parser = argparse.ArgumentParser(
        description="智能仓储机器人调度系统 — 离线评测"
    )
    parser.add_argument(
        "--mode",
        choices=["planner", "agent", "compare", "all"],
        default="all",
        help="评测模式（默认 all，同时运行规划器 + Agent）",
    )
    parser.add_argument(
        "--model",
        default="deepseek-chat",
        help="Agent 评测使用的模型名（默认 deepseek-chat）",
    )
    parser.add_argument(
        "--models",
        default="",
        help="多模型对比的模型列表，逗号分隔（如 deepseek-chat,gpt-4o-mini）",
    )
    parser.add_argument(
        "--dataset",
        choices=["scenario", "nlu_tool", "edge_input", "all"],
        default="all",
        help="Agent 评测数据集（默认 all）",
    )
    parser.add_argument(
        "--output-dir",
        default="eval/reports",
        help="报告输出目录",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="基线 JSON 文件路径（可选）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
        help="是否输出详细日志（默认 True）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式（覆盖 --verbose）",
    )
    parser.add_argument(
        "--max-timestep",
        default=200,
        type=int,
        help="最大时间步（默认 200）",
    )
    parser.add_argument(
        "--model-config",
        default=None,
        help="模型配置文件路径（默认从 configs/api_config.json 读取）",
    )
    args = parser.parse_args()

    verbose = not args.quiet if args.quiet else args.verbose
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    all_metrics = {}
    all_results = []

    # ═══════════════════════════════════════════════════════════════════════
    # 层面 1：规划器评测（确定性内核，无需 LLM）
    # ═══════════════════════════════════════════════════════════════════════
    if args.mode in ("planner", "all"):
        if verbose:
            print(f"\n{'='*60}")
            print(f"  层面 1：规划器评测（规划内核）")
            print(f"{'='*60}")

        results, metrics = run_scenario_eval(
            dataset_path="eval/datasets/scenario_test.json",
            max_timestep=args.max_timestep,
            verbose=verbose,
        )
        all_results.extend(results)
        all_metrics["scenario"] = metrics

        generate_json_report(
            results, metrics,
            dataset_name="scenario",
            output_path=f"{output_dir}/scenario_{timestamp}.json",
        )
        md = generate_markdown_report(
            results, metrics,
            dataset_name="场景评测",
            output_path=f"{output_dir}/scenario_{timestamp}.md",
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 层面 2：Agent 评测（需要 LLM API）
    # ═══════════════════════════════════════════════════════════════════════
    if args.mode in ("agent", "all", "compare"):
        if verbose:
            print(f"\n{'='*60}")
            print(f"  层面 2：Agent 评测（LLM 工具调用）")
            print(f"{'='*60}")

        # 加载模型配置
        configs = load_model_configs(args.model_config)
        if not configs:
            if verbose:
                print(f"  [WARN] No model config found, using default from env")
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            configs = [ModelConfig(
                name="deepseek-chat",
                model_id="deepseek-chat",
                api_key=api_key,
                base_url="https://api.deepseek.com/v1",
            )]

        # 确定要评测的数据集
        agent_datasets = []
        if args.dataset in ("nlu_tool", "all"):
            agent_datasets.append(("nlu_tool", "eval/datasets/nlu_tool_test.json"))
        if args.dataset in ("edge_input", "all"):
            agent_datasets.append(("edge_input", "eval/datasets/edge_input_test.json"))

        # 选择模型
        model_names = [m.strip() for m in args.models.split(",") if m.strip()]
        if not model_names:
            model_names = [args.model]

        # 对每个模型运行 Agent 评测
        all_model_results = {}
        for model_name in model_names:
            # 查找匹配的配置
            config = next((c for c in configs if c.name == model_name), None)
            if config is None:
                print(f"  [WARN] No config for model '{model_name}', using default")
                config = ModelConfig(
                    name=model_name,
                    model_id=model_name,
                    api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                    base_url="https://api.deepseek.com/v1",
                )

            if verbose:
                print(f"\n  --- 模型: {config.name} ---")

            client = config.get_client()

            for dset_name, dset_path in agent_datasets:
                if verbose:
                    print(f"\n  数据集: {dset_name}")

                tm = make_tool_manager(
                    llm_client=client,
                    model_name=config.model_id,
                )
                results, metrics = run_agent_eval(
                    dataset_path=dset_path,
                    tool_manager=tm,
                    verbose=verbose,
                )
                all_results.extend(results)
                key = f"agent_{dset_name}_{config.name}"
                all_metrics[key] = metrics

                if model_name not in all_model_results:
                    all_model_results[model_name] = {"metrics": {}, "results": []}
                all_model_results[model_name]["metrics"][dset_name] = metrics
                all_model_results[model_name]["results"].extend(results)

                # 生成单模型报告
                generate_agent_report(
                    results, metrics,
                    dataset_name=f"{dset_name} ({config.name})",
                    model_name=config.name,
                    output_path=f"{output_dir}/agent_{dset_name}_{config.name}_{timestamp}.md",
                )

                # 保存 JSON
                json_path = f"{output_dir}/agent_{dset_name}_{config.name}_{timestamp}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "model": config.name,
                        "dataset": dset_name,
                        "metrics": metrics,
                        "results": [
                            {k: v for k, v in r.items() if k != "comparison"}
                            for r in results
                        ],
                    }, f, ensure_ascii=False, indent=2)

        # ═══════════════════════════════════════════════════════════════════
        # 层面 3：多模型对比
        # ═══════════════════════════════════════════════════════════════════
        if args.mode == "compare" and len(all_model_results) >= 2:
            if verbose:
                print(f"\n{'='*60}")
                print(f"  层面 3：多模型对比")
                print(f"{'='*60}")

            # 合并每个模型的指标
            compare_data = {}
            for mname, mdata in all_model_results.items():
                combined_metrics = {}
                for dset_metrics in mdata["metrics"].values():
                    for k, v in dset_metrics.items():
                        if k not in combined_metrics:
                            combined_metrics[k] = v
                compare_data[mname] = {
                    "metrics": combined_metrics,
                    "results": mdata["results"],
                }

            generate_model_comparison(
                compare_data,
                output_path=f"{output_dir}/model_comparison_{timestamp}.md",
            )

    # ═══════════════════════════════════════════════════════════════════════
    # 汇总报告
    # ═══════════════════════════════════════════════════════════════════════
    if all_metrics:
        summary = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "datasets": list(all_metrics.keys()),
        }
        for k, v in all_metrics.items():
            summary[k] = v

        summary_path = f"{output_dir}/summary_{timestamp}.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"\n  [INFO] Summary saved to {summary_path}")

    # ═══════════════════════════════════════════════════════════════════════
    # 基线对比
    # ═══════════════════════════════════════════════════════════════════════
    if args.baseline and os.path.exists(args.baseline):
        if verbose:
            print(f"\n  [INFO] Comparing with baseline: {args.baseline}")
        try:
            with open(args.baseline, "r", encoding="utf-8") as f:
                baseline = json.load(f)

            latest = summary
            diff = compare_baselines(
                latest.get("scenario", {}),
                baseline.get("scenario", {}),
            )
            if verbose:
                print(f"  改进: {len(diff['improvements'])} 项")
                for imp in diff["improvements"][:5]:
                    print(f"    ✅ {imp}")
                print(f"  退化: {len(diff['regressions'])} 项")
                for reg in diff["regressions"][:5]:
                    print(f"    ⚠ {reg}")
        except Exception as e:
            print(f"  [ERROR] Baseline comparison failed: {e}")

    if verbose:
        print(f"\n{'='*60}")
        print(f"  评测完成! 报告已保存至 {output_dir}/")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()

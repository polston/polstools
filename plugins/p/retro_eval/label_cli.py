"""Commands for importing and reporting local label datasets."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from .annotation import (import_annotations, predict_annotations,
                         render_annotation_guide, sample_annotations,
                         validate_prediction_artifact)
from .annotation_ui import AnnotationWorkspace, serve_annotation_ui
from .catalog import (load_annotation_protocol_catalogue,
                      load_rubric_catalogue)
from .labels import (LabelStore, import_legacy_turn_labels,
                     multiclass_calibration_report,
                     strict_multiclass_comparison_report)
from .predictors import load_predictor


def _current_legacy_predictor():
    path = Path(__file__).resolve().parents[1] / "bin" / "retro.py"
    spec = importlib.util.spec_from_file_location("retro_eval_legacy_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda sample: module._predict_at(
        sample, module.CORRECTION_MAX_CHARS, module.CORRECTION_MIN_PRIOR_CHARS)


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-legacy")
    importer.add_argument("--source", type=Path, required=True)
    importer.add_argument("--labels", type=Path, required=True)
    importer.add_argument("--predictions", type=Path, required=True)
    reporter = commands.add_parser("report")
    reporter.add_argument("--labels", type=Path, required=True)
    reporter.add_argument("--predictions", type=Path, required=True)
    reporter.add_argument("--output", type=Path, required=True)
    reporter.add_argument("--split", action="append", choices=("calibration", "test"))
    sampler = commands.add_parser("sample")
    sampler.add_argument("--extract", type=Path, action="append", required=True)
    sampler.add_argument("--output", type=Path, required=True)
    sampler.add_argument("--manifest", type=Path, required=True)
    sampler.add_argument("--per-source", type=int, default=20)
    sampler.add_argument("--dataset-id", default="turn-friction-heldout-v1")
    sampler.add_argument("--rubric-id", default="turn_friction_legacy")
    sampler.add_argument("--protocol-id", default="turn-friction-dominant-intent")
    sampler.add_argument("--protocol-version", type=int, default=2)
    sampler.add_argument("--split", choices=("calibration", "test"), default="test")
    sampler.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    sampler.add_argument(
        "--protocols", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" /
        "annotation-protocols.json")
    annotation_import = commands.add_parser("import-annotations")
    annotation_import.add_argument("--source", type=Path, required=True)
    annotation_import.add_argument("--labels", type=Path, required=True)
    annotation_import.add_argument("--manifest", type=Path, required=True)
    annotation_import.add_argument("--rubric-id", default="turn_friction_legacy")
    annotation_import.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    predictor = commands.add_parser("predict-annotations")
    predictor.add_argument("--source", type=Path, required=True)
    predictor.add_argument("--predictions", type=Path, required=True)
    predictor.add_argument("--manifest", type=Path, required=True)
    predictor.add_argument("--prediction-manifest", type=Path, required=True)
    predictor.add_argument("--created-commit", required=True)
    predictor.add_argument("--rubric-id", default="turn_friction_legacy")
    predictor.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    comparison = commands.add_parser("compare")
    comparison.add_argument("--labels", type=Path, required=True)
    comparison.add_argument("--predictions", action="append", required=True,
                            metavar="NAME=PATH")
    comparison.add_argument("--prediction-manifest", action="append", required=True,
                            metavar="NAME=PATH")
    comparison.add_argument("--sample-manifest", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    comparison.add_argument("--split", action="append", choices=("calibration", "test"))
    comparison.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    guide = commands.add_parser("guide")
    guide.add_argument("--output", type=Path, required=True)
    guide.add_argument("--protocol-id", default="turn-friction-dominant-intent")
    guide.add_argument("--protocol-version", type=int, default=2)
    guide.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    guide.add_argument(
        "--protocols", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" /
        "annotation-protocols.json")
    server = commands.add_parser("serve")
    server.add_argument("--source", type=Path, required=True)
    server.add_argument("--manifest", type=Path, required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=0)
    server.add_argument("--no-open", action="store_true")
    server.add_argument(
        "--rubrics", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" / "rubrics.json")
    server.add_argument(
        "--protocols", type=Path,
        default=Path(__file__).resolve().parents[1] / "rubrics" /
        "annotation-protocols.json")
    args = parser.parse_args(argv)
    if args.command == "import-legacy":
        result = import_legacy_turn_labels(
            args.source, args.labels, args.predictions,
            predictor=_current_legacy_predictor())
    elif args.command == "report":
        truth = LabelStore(args.labels).read()
        predicted = LabelStore(args.predictions).read()
        labels = sorted({item.label for item in truth} | {item.label for item in predicted})
        result = multiclass_calibration_report(
            truth, predicted, labels=labels,
            splits=tuple(args.split or ("calibration", "test")),
        )
        LabelStore(args.output)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    elif args.command == "sample":
        catalogue = load_rubric_catalogue(args.rubrics)
        try:
            rubric = next(item for item in catalogue.rubrics if item.id == args.rubric_id)
        except StopIteration as exc:
            raise ValueError("rubric id is absent from catalogue") from exc
        protocols = load_annotation_protocol_catalogue(args.protocols, catalogue)
        protocol = protocols.get(args.protocol_id, args.protocol_version)
        if (protocol.rubric_id != rubric.id
                or protocol.rubric_version != rubric.version):
            raise ValueError("annotation protocol does not match selected rubric")
        result = sample_annotations(
            args.extract, args.output, args.manifest, per_source=args.per_source,
            dataset_id=args.dataset_id, rubric_id=rubric.id,
            rubric_version=rubric.version, split=args.split,
            annotation_protocol=protocol)
    elif args.command in {"import-annotations", "predict-annotations"}:
        catalogue = load_rubric_catalogue(args.rubrics)
        try:
            rubric = next(item for item in catalogue.rubrics if item.id == args.rubric_id)
        except StopIteration as exc:
            raise ValueError("rubric id is absent from catalogue") from exc
        if args.command == "import-annotations":
            result = import_annotations(
                args.source, args.labels, manifest_path=args.manifest,
                rubric_id=rubric.id, rubric_version=rubric.version,
                allowed_labels=rubric.labels)
        else:
            predictor_spec = str(rubric.extensions.get("deterministic_predictor") or "")
            predictor_id = str(rubric.extensions.get("deterministic_predictor_id") or "")
            if not predictor_spec or not predictor_id:
                raise ValueError("rubric has no configured deterministic predictor")
            result = predict_annotations(
                args.source, args.predictions, manifest_path=args.manifest,
                prediction_manifest_path=args.prediction_manifest,
                allowed_labels=rubric.labels, predictor=load_predictor(predictor_spec),
                predictor_id=predictor_id, created_commit=args.created_commit,
                predictor_config=predictor_spec)
    elif args.command == "guide":
        catalogue = load_rubric_catalogue(args.rubrics)
        protocol = load_annotation_protocol_catalogue(
            args.protocols, catalogue).get(args.protocol_id, args.protocol_version)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_annotation_guide(protocol), encoding="utf-8")
        result = {"protocol_id": protocol.id, "protocol_version": protocol.version,
                  "protocol_sha256": protocol.sha256}
    elif args.command == "serve":
        workspace = AnnotationWorkspace(
            source=args.source, manifest_path=args.manifest,
            rubrics_path=args.rubrics, protocols_path=args.protocols)
        serve_annotation_ui(
            workspace, host=args.host, port=args.port,
            open_browser=not args.no_open)
        return 0
    else:
        truth = LabelStore(args.labels).read()
        if not truth:
            raise ValueError("comparison requires human truth")
        catalogue = load_rubric_catalogue(args.rubrics)
        try:
            rubric = next(item for item in catalogue.rubrics
                          if item.id == truth[0].rubric_id
                          and item.version == truth[0].rubric_version)
        except StopIteration as exc:
            raise ValueError("truth rubric is absent from catalogue") from exc
        predictions = {}
        prediction_paths = {}
        for value in args.predictions:
            name, separator, raw_path = value.partition("=")
            if not separator or not name or not raw_path or name in predictions:
                raise ValueError("predictions must be unique NAME=PATH values")
            prediction_paths[name] = Path(raw_path)
            predictions[name] = LabelStore(prediction_paths[name]).read()
        manifests = {}
        for value in args.prediction_manifest:
            name, separator, raw_path = value.partition("=")
            if not separator or not name or not raw_path or name in manifests:
                raise ValueError("prediction manifests must be unique NAME=PATH values")
            manifests[name] = Path(raw_path)
        if set(manifests) != set(predictions):
            raise ValueError("every predictor requires one matching manifest")
        for name in predictions:
            validate_prediction_artifact(
                prediction_paths[name], manifests[name],
                sample_manifest_path=args.sample_manifest)
        result = strict_multiclass_comparison_report(
            truth, predictions, labels=rubric.labels,
            splits=tuple(args.split or ("test",)))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

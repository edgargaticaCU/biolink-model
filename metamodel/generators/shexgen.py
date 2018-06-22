"""Generate RDF

"""
import os
from typing import Union, TextIO, Optional, List

import click
from ShExJSG import ShExC
from ShExJSG.SchemaWithContext import Schema
from ShExJSG.ShExJ import Shape, IRIREF, ShapeAnd, EachOf, TripleConstraint, NodeConstraint, shapeExpr, ShapeOr
from jsonasobj import as_json
from rdflib import Graph, XSD, OWL, RDF
from prefixcommons import curie_util as cu

from metamodel.metamodel import SchemaDefinition, ClassDefinition, SlotDefinition, ClassDefinitionName, \
    SlotDefinitionName
from metamodel.utils.builtins import builtin_names, builtin_uri
from metamodel.utils.formatutils import camelcase, underscore
from metamodel.utils.generator import Generator
from metamodel.utils.namespaces import BIOENTITY, META


class ShExGenerator(Generator):
    generatorname = os.path.basename(__file__)
    generatorversion = "0.0.2"
    valid_formats = ['shex', 'json', 'rdf']
    visit_all_class_slots = False

    def __init__(self, schema: Union[str, TextIO, SchemaDefinition], fmt: str = 'shex') -> None:
        super().__init__(schema, fmt)
        self.shex: Schema = None
        self.shape: Shape = None                # Shape being defined
        self.list_shapes: List[IRIREF] = None

    def visit_schema(self, **_):
        self.shex = Schema()
        self.shex.shapes = []
        self.list_shapes = []
        if self.format == 'shex':
            raise NotImplementedError("ShExC format is not yet implemented")
        self.add_builtins()
        # TODO: Imports

    @staticmethod
    def _shapeIRI(name: ClassDefinitionName) -> IRIREF:
        return IRIREF(BIOENTITY[camelcase(name)])

    def _predicate(self, name: SlotDefinitionName) -> IRIREF:
        slot = self.schema.slots[name]
        if slot.mappings:
            return IRIREF(cu.expand_uri(slot.mappings[0]))
        else:
            # TODO: look at the RDF to figure out what URI's go here
            return IRIREF(BIOENTITY[underscore(name)])

    def visit_class(self, cls: ClassDefinition) -> bool:
        self.shape = Shape()
        # if not cls.mixin and not cls.name in self.synopsis.mixinrefs and not cls.abstract:
        #     self.shapeExpr.closed = jsg.Boolean(True)
        # # TODO: Add this when shex 2.1 is committed
        # if cls.abstract:
        #     self.shapeExpr.abstract = True
        # TODO: Figure out the semantics of union_of
        # TODO: symmetric
        return True

    def end_class(self, cls: ClassDefinition) -> None:
        if cls.is_a or cls.mixins:
            shapeExpr = ShapeAnd(shapeExprs=([self._shapeIRI(cls.is_a)] if cls.is_a else []) +
                                                 [self._shapeIRI(mixin) for mixin in cls.mixins] + [self.shape])
        else:
            shapeExpr = self.shape
        shapeExpr.id = self._shapeIRI(cls.name)
        self.shex.shapes.append(shapeExpr)

    def _type_constraint(self, rnge: Optional[str]) -> NodeConstraint:
        # TODO: missing type - string or '.'?
        if rnge in builtin_names:
            return NodeConstraint(datatype=IRIREF(XSD[rnge]))
        elif rnge in self.schema.types:
            return self._type_constraint(self.schema.types[rnge].typeof)
        else:
            return NodeConstraint()

    def visit_class_slot(self, cls: ClassDefinition, aliased_slot_name: str, slot: SlotDefinition) -> None:
        constraint = TripleConstraint()
        # Juggling to get the constraint to be either a single triple constraint or an eachof construct
        if not self.shape.expression:
            self.shape.expression = constraint
        elif isinstance(self.shape.expression, TripleConstraint):
            self.shape.expression = EachOf(expressions=[self.shape.expression])
            self.shape.expression.expressions.append(constraint)
        else:
            self.shape.expression.expressions.append(constraint)

        constraint.predicate = self._predicate(slot.name)
        # JSON-LD generates multi-valued entries as lists
        constraint.min = 1 if slot.primary_key or slot.required else 0
        constraint.max = 1
        # TODO: This should not be hard coded -- figure out where to go with it
        rng = IRIREF(META.SlotRangeTypes) if slot.range == 'anytype' else\
              self._type_constraint(slot.range) if slot.range and slot.range not in self.schema.classes else\
              self._shapeIRI(slot.range)
        name_base = ("XSD_" + slot.range) if isinstance(rng, NodeConstraint) else str(rng)
        constraint.valueExpr = self.gen_multivalued_slot(name_base, rng) if slot.multivalued else rng

    def gen_multivalued_slot(self, target_name_base: str, target_type: IRIREF) -> IRIREF:
        """ Generate a shape that represents an RDF list of target_type

        @param target_name_base:
        @param target_type:
        @return:
        """
        list_shape_id = IRIREF(target_name_base + "__List")
        if list_shape_id not in self.list_shapes:
            list_shape = Shape(id=list_shape_id, closed=True)
            list_shape.expression = EachOf()
            list_shape.expression.expressions = [TripleConstraint(predicate=RDF.first, valueExpr=target_type, min=0, max=1)]
            targets = ShapeOr()
            targets.shapeExprs = [(NodeConstraint(values=[RDF.nil]))]
            targets.shapeExprs.append(list_shape_id)
            list_shape.expression.expressions.append(
                TripleConstraint(predicate=RDF.rest, valueExpr=targets))
            self.shex.shapes.append(list_shape)
            self.list_shapes.append(list_shape_id)
        return list_shape_id

    def end_schema(self, output: Optional[str]) -> None:
        shex = as_json(self.shex)
        if self.format == 'rdf':
            g = Graph()
            g.parse(data=shex, format="json-ld")
            g.bind('owl', OWL)
            g.bind('biolink', BIOENTITY)
            g.bind('meta', META)
            shex = g.serialize(format='turtle').decode()
        elif self.format == 'shex':
            # TODO: wait until the better ShExC emitter is committed
            shex = str(ShExC(self.shex))
        if output:
            with open(output, 'w') as outf:
                outf.write(shex)
        else:
            print(shex)

    def add_builtins(self):
        # TODO:  At some point we should get rid of the hard-coded builtins and add a set of TypeDefinitions for
        builtin_valueset = NodeConstraint(id=META.Builtins,
                                          values=[IRIREF(builtin_uri(name, expand=True)) for name in builtin_names] +
                                                 [BIOENTITY.anytype])
        self.shex.shapes.append(builtin_valueset)
        range_type_choices = ShapeOr(id=META.SlotRangeTypes,
                                     shapeExprs=[BIOENTITY.TypeDefinition, BIOENTITY.ClassDefinition, META.Builtins])
        self.shex.shapes.append(range_type_choices)



@click.command()
@click.argument("yamlfile", type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "-f", default='shex', type=click.Choice(ShExGenerator.valid_formats),
              help="Output format")
@click.option("-o", "--output", help="Output file name")
def cli(yamlfile, format, output):
    """ Generate a ShEx Schema for a  biolink model """
    print(ShExGenerator(yamlfile, format).serialize(output=output))

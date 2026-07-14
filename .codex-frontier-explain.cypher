EXPLAIN
MATCH (source:CanonicalGraphNode {graph_version: '4ff05189d29ffc3e1891d8bbada1b4608c05b638a7cf3bb30bc48c040ffff75a', external_id: 'hades:node:v1:02c3becb554cbc0b793a7b12158c0a3411179278a5b08e685780ff59f86c7349'})
MATCH (source)-[edge]-(target:CanonicalGraphNode {graph_version: '4ff05189d29ffc3e1891d8bbada1b4608c05b638a7cf3bb30bc48c040ffff75a'})
WHERE edge.graph_version = '4ff05189d29ffc3e1891d8bbada1b4608c05b638a7cf3bb30bc48c040ffff75a'
WITH source, target, edge
ORDER BY target.external_id, edge.external_id
LIMIT 6
RETURN properties(target) AS node, properties(edge) AS edge;

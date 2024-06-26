/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ignite.internal.sql;

import org.apache.ignite.internal.sql.command.SqlCommand;
import org.apache.ignite.internal.sql.command.SqlCreateIndexCommand;
import org.apache.ignite.internal.sql.command.SqlDropUserCommand;
import org.junit.Test;

/**
 * Parser test for multi-statement queries.
 */
public class SqlParserMultiStatementSelfTest extends SqlParserAbstractSelfTest {
    /**
     * Check that empty statements don't affect regular ones.
     */
    @Test
    public void testEmptyStatements() {
        String sql = ";;;CREATE INDEX TEST on TABLE1(id)  ; ;   DROP USER test   ;;;";

        SqlParser parser = new SqlParser("schema", sql);

        // Haven't parse anything yet.
        assertEquals(null, parser.lastCommandSql());
        assertEquals(sql, parser.remainingSql());

        SqlCommand create = parser.nextCommand();

        assertTrue(create instanceof SqlCreateIndexCommand);
        assertEquals("CREATE INDEX TEST on TABLE1(id)", parser.lastCommandSql());
        assertEquals(" ;   DROP USER test   ;;;", parser.remainingSql());

        SqlCommand dropUser = parser.nextCommand();

        assertTrue(dropUser instanceof SqlDropUserCommand);
        assertEquals("DROP USER test", parser.lastCommandSql());
        assertEquals(";;", parser.remainingSql());

        SqlCommand emptyCmd = parser.nextCommand();
        assertEquals(null, emptyCmd);
        assertEquals(null, parser.lastCommandSql());
        assertEquals(null, parser.remainingSql());
    }

    /**
     * Check that comments between statements work.
     */
    @Test
    public void testComments() {
        String sql = " -- Creating new index \n" +
            " CREATE INDEX IDX1 on TABLE1(id); \n" +
            " -- Creating one more index \n" +
            " CREATE INDEX IDX2 on TABLE2(id); \n" +
            " -- All done.";

        SqlParser parser = new SqlParser("schema", sql);

        // Haven't parse anything yet.
        assertEquals(null, parser.lastCommandSql());
        assertEquals(sql, parser.remainingSql());

        SqlCommand cmd = parser.nextCommand();

        assertTrue(cmd instanceof SqlCreateIndexCommand);
        assertEquals("CREATE INDEX IDX1 on TABLE1(id)", parser.lastCommandSql());
        assertEquals(" \n -- Creating one more index \n" +
            " CREATE INDEX IDX2 on TABLE2(id); \n" +
            " -- All done.", parser.remainingSql());

        cmd = parser.nextCommand();

        assertTrue(cmd instanceof SqlCreateIndexCommand);
        assertEquals("CREATE INDEX IDX2 on TABLE2(id)", parser.lastCommandSql());
        assertEquals(" \n -- All done.", parser.remainingSql());

        cmd = parser.nextCommand();

        assertNull(cmd);
        assertEquals(null, parser.lastCommandSql());
        assertEquals(null, parser.remainingSql());
    }
}

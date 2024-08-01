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

package org.apache.ignite.internal.management.cache;

import java.io.File;
import java.io.IOException;
import java.io.ObjectInput;
import java.io.ObjectOutput;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;
import org.apache.ignite.IgniteException;
import org.apache.ignite.internal.dto.IgniteDataTransferObject;
import org.apache.ignite.internal.management.api.Argument;
import org.apache.ignite.internal.util.typedef.internal.U;

import static java.lang.String.format;

/** */
public class CacheCreateCommandArg extends IgniteDataTransferObject {
    /** */
    private static final long serialVersionUID = 0;

    /** */
    @Argument(description = "Path to the Spring XML configuration that contains " +
        "'org.apache.ignite.configuration.CacheConfiguration' beans to create caches from", example = "springXmlConfigPath")
    private String springxmlconfig;

    /** */
    @Argument(description = "Optional flag to list caches to ignore at creation step. " +
        "You can use regular expressions to list caches", optional = true, example = "cacheName1,...,cacheNameN")
    private String[] excludeCaches;

    /** */
    private String fileContent;

    /** */
    private void readFile() {
        if (!new File(springxmlconfig).exists()) {
            throw new IgniteException("Failed to create caches. Spring XML configuration file not found " +
                "[file=" + springxmlconfig + ']');
        }

        try {
            fileContent = U.readFileToString(springxmlconfig, "UTF-8");
        }
        catch (IOException e) {
            throw new IgniteException("Failed to create caches. Failed to read Spring XML configuration file " +
                "[file=" + springxmlconfig + ']', e);
        }
    }

    /**
     * @param str To validate that given name is valed regex.
     */
    private void validateRegexes(String[] str) {
        for (String s : str) {
            try {
                Pattern.compile(s);
            }
            catch (PatternSyntaxException e) {
                throw new IgniteException(format("Invalid cache name regexp '%s': %s", s, e.getMessage()));
            }
        }
    }

    /** {@inheritDoc} */
    @Override protected void writeExternalData(ObjectOutput out) throws IOException {
        U.writeString(out, springxmlconfig);
        U.writeArray(out, excludeCaches);
        U.writeString(out, fileContent);
    }

    /** {@inheritDoc} */
    @Override protected void readExternalData(byte protoVer, ObjectInput in) throws IOException, ClassNotFoundException {
        springxmlconfig = U.readString(in);
        excludeCaches = U.readArray(in, String.class);
        fileContent = U.readString(in);
    }

    /** */
    public String springxmlconfig() {
        return springxmlconfig;
    }

    /** */
    public void springxmlconfig(String springxmlconfig) {
        this.springxmlconfig = springxmlconfig;
        readFile();
    }

    /** */
    public String[] excludeCaches() {
        return excludeCaches;
    }

    /** */
    public void excludeCaches(String[] excludeCaches) {
        this.excludeCaches = excludeCaches;

        validateRegexes(excludeCaches);
    }

    /** */
    public void fileContent(String fileContent) {
        this.fileContent = fileContent;
    }

    /** */
    public String fileContent() {
        return fileContent;
    }
}
